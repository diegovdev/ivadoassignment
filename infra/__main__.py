"""Museums app — ECS Fargate + RDS PostgreSQL on AWS.

Stack config (set per environment via Pulumi.<stack>.yaml):
    museums:environment          dev | staging | prod
    museums:az_count             2 (use 3 for staging/prod)
    museums:api_cpu              256
    museums:api_memory           512
    museums:ecs_api_desired      1
    museums:ecs_api_min          1
    museums:ecs_api_max          3
    museums:db_instance_class    db.t3.micro
    museums:db_multi_az          false
    museums:db_deletion_protection false
    museums:log_retention_days   7
    museums:rds_auto_shutdown    false
    museums:alarm_email          (required)

Deploy workflow:
    1. pulumi up                              # provision infra
    2. docker build / push to ECR             # see outputs for repo URLs
    3. curl $(pulumi stack output api_url)/ingest -X POST
"""

import json

import pulumi
import pulumi_aws as aws

# ── Configuration ─────────────────────────────────────────────────────────────
cfg = pulumi.Config()
stack = pulumi.get_stack()
prefix = f"museums-{stack}"

environment = cfg.get("environment") or stack
az_count = cfg.get_int("az_count") or 2
api_cpu = cfg.get("api_cpu") or "256"
api_memory = cfg.get("api_memory") or "512"
ecs_api_desired = cfg.get_int("ecs_api_desired") or 1
ecs_api_min = cfg.get_int("ecs_api_min") or 1
ecs_api_max = cfg.get_int("ecs_api_max") or 3
db_instance_class = cfg.get("db_instance_class") or "db.t3.micro"
db_multi_az = cfg.get_bool("db_multi_az") or False
db_deletion_protection = cfg.get_bool("db_deletion_protection") or False
log_retention_days = cfg.get_int("log_retention_days") or 7
rds_auto_shutdown = cfg.get_bool("rds_auto_shutdown") or False
alarm_email = cfg.require("alarm_email")

azs = aws.get_availability_zones(state="available")
region = aws.get_region().name

# ── Section A — VPC ───────────────────────────────────────────────────────────
vpc = aws.ec2.Vpc(
    f"{prefix}-vpc",
    cidr_block="10.0.0.0/16",
    enable_dns_hostnames=True,
    enable_dns_support=True,
    tags={"Name": prefix},
)

igw = aws.ec2.InternetGateway(f"{prefix}-igw", vpc_id=vpc.id)

# Public subnets — ALB lives here
public_subnets = [
    aws.ec2.Subnet(
        f"{prefix}-public-{i}",
        vpc_id=vpc.id,
        cidr_block=f"10.0.{i}.0/24",
        availability_zone=azs.names[i],
        map_public_ip_on_launch=True,
        tags={"Name": f"{prefix}-public-{i}"},
    )
    for i in range(az_count)
]

# Private subnets — ECS tasks and RDS live here
private_subnets = [
    aws.ec2.Subnet(
        f"{prefix}-private-{i}",
        vpc_id=vpc.id,
        cidr_block=f"10.0.{i + 10}.0/24",
        availability_zone=azs.names[i],
        tags={"Name": f"{prefix}-private-{i}"},
    )
    for i in range(az_count)
]

# Public route table → Internet Gateway
public_rt = aws.ec2.RouteTable(
    f"{prefix}-public-rt",
    vpc_id=vpc.id,
    routes=[aws.ec2.RouteTableRouteArgs(cidr_block="0.0.0.0/0", gateway_id=igw.id)],
)
for i, sn in enumerate(public_subnets):
    aws.ec2.RouteTableAssociation(
        f"{prefix}-pub-rta-{i}", subnet_id=sn.id, route_table_id=public_rt.id
    )

# Single NAT Gateway in first public subnet (cost-optimised)
eip = aws.ec2.Eip(f"{prefix}-nat-eip", domain="vpc")
nat_gw = aws.ec2.NatGateway(
    f"{prefix}-nat", subnet_id=public_subnets[0].id, allocation_id=eip.id
)

# Private route table → NAT Gateway (allows ECR pulls, outbound API calls)
private_rt = aws.ec2.RouteTable(
    f"{prefix}-private-rt",
    vpc_id=vpc.id,
    routes=[aws.ec2.RouteTableRouteArgs(cidr_block="0.0.0.0/0", nat_gateway_id=nat_gw.id)],
)
for i, sn in enumerate(private_subnets):
    aws.ec2.RouteTableAssociation(
        f"{prefix}-priv-rta-{i}", subnet_id=sn.id, route_table_id=private_rt.id
    )

# ── Section B — Security Groups ────────────────────────────────────────────────
alb_sg = aws.ec2.SecurityGroup(
    f"{prefix}-alb-sg",
    vpc_id=vpc.id,
    ingress=[
        aws.ec2.SecurityGroupIngressArgs(
            from_port=80, to_port=80, protocol="tcp", cidr_blocks=["0.0.0.0/0"],
            description="API traffic",
        ),
        aws.ec2.SecurityGroupIngressArgs(
            from_port=8888, to_port=8888, protocol="tcp", cidr_blocks=["0.0.0.0/0"],
            description="Jupyter traffic",
        ),
    ],
    egress=[
        aws.ec2.SecurityGroupEgressArgs(
            from_port=0, to_port=0, protocol="-1", cidr_blocks=["0.0.0.0/0"]
        )
    ],
)

api_sg = aws.ec2.SecurityGroup(
    f"{prefix}-api-sg",
    vpc_id=vpc.id,
    ingress=[
        aws.ec2.SecurityGroupIngressArgs(
            from_port=8000, to_port=8000, protocol="tcp",
            security_groups=[alb_sg.id], description="From ALB only",
        )
    ],
    egress=[
        aws.ec2.SecurityGroupEgressArgs(
            from_port=0, to_port=0, protocol="-1", cidr_blocks=["0.0.0.0/0"]
        )
    ],
)

jupyter_sg = aws.ec2.SecurityGroup(
    f"{prefix}-jupyter-sg",
    vpc_id=vpc.id,
    ingress=[
        aws.ec2.SecurityGroupIngressArgs(
            from_port=8888, to_port=8888, protocol="tcp",
            security_groups=[alb_sg.id], description="From ALB only",
        )
    ],
    egress=[
        aws.ec2.SecurityGroupEgressArgs(
            from_port=0, to_port=0, protocol="-1", cidr_blocks=["0.0.0.0/0"]
        )
    ],
)

# RDS only accepts connections from the API task
rds_sg = aws.ec2.SecurityGroup(
    f"{prefix}-rds-sg",
    vpc_id=vpc.id,
    ingress=[
        aws.ec2.SecurityGroupIngressArgs(
            from_port=5432, to_port=5432, protocol="tcp",
            security_groups=[api_sg.id], description="From API task only",
        )
    ],
    egress=[
        aws.ec2.SecurityGroupEgressArgs(
            from_port=0, to_port=0, protocol="-1", cidr_blocks=["0.0.0.0/0"]
        )
    ],
)

# ── Section C — Secrets Manager + RDS ─────────────────────────────────────────
db_subnet_group = aws.rds.SubnetGroup(
    f"{prefix}-db-subnets",
    subnet_ids=[s.id for s in private_subnets],
)

# AWS manages password creation and rotation (rotates every 7 days).
# The secret ARN is available via db.master_user_secrets[0].secret_arn.
db = aws.rds.Instance(
    f"{prefix}-db",
    engine="postgres",
    engine_version="16",
    instance_class=db_instance_class,
    allocated_storage=20,
    db_name="museums",
    username="postgres",
    manage_master_user_password=True,
    db_subnet_group_name=db_subnet_group.name,
    vpc_security_group_ids=[rds_sg.id],
    multi_az=db_multi_az,
    skip_final_snapshot=True,
    deletion_protection=db_deletion_protection,
    tags={"Name": f"{prefix}-db"},
)

# ── Section D — RDS Auto-Shutdown ─────────────────────────────────────────────
_RDS_SCHEDULER_CODE = """\
import boto3
import json
import os

def handler(event, context):
    rds = boto3.client('rds')
    action = event.get('action', 'stop')
    db_id = os.environ['DB_INSTANCE_ID']
    try:
        if action == 'stop':
            rds.stop_db_instance(DBInstanceIdentifier=db_id)
        else:
            rds.start_db_instance(DBInstanceIdentifier=db_id)
        return {'status': f'{action}ped {db_id}'}
    except rds.exceptions.InvalidDBInstanceStateFault as e:
        print(f'Instance already in target state: {e}')
        return {'status': 'already in target state'}
"""

if rds_auto_shutdown:
    lambda_role = aws.iam.Role(
        f"{prefix}-rds-scheduler-role",
        assume_role_policy=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }],
        }),
    )

    aws.iam.RolePolicy(
        f"{prefix}-rds-scheduler-policy",
        role=lambda_role.name,
        policy=json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "rds:StopDBInstance",
                        "rds:StartDBInstance",
                    ],
                    "Resource": "*",
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                    ],
                    "Resource": "*",
                },
            ],
        }),
    )

    rds_lambda = aws.lambda_.Function(
        f"{prefix}-rds-scheduler",
        code=pulumi.AssetArchive({"index.py": pulumi.StringAsset(_RDS_SCHEDULER_CODE)}),
        role=lambda_role.arn,
        handler="index.handler",
        runtime="python3.12",
        timeout=30,
        environment=aws.lambda_.FunctionEnvironmentArgs(
            variables={"DB_INSTANCE_ID": db.identifier}
        ),
    )

    stop_rule = aws.cloudwatch.EventRule(
        f"{prefix}-rds-stop",
        schedule_expression="cron(0 20 * * ? *)",
        description="Stop RDS at 8pm UTC daily",
    )
    start_rule = aws.cloudwatch.EventRule(
        f"{prefix}-rds-start",
        schedule_expression="cron(0 7 * * ? *)",
        description="Start RDS at 7am UTC daily",
    )

    aws.cloudwatch.EventTarget(
        f"{prefix}-rds-stop-target",
        rule=stop_rule.name,
        arn=rds_lambda.arn,
        input=json.dumps({"action": "stop"}),
    )
    aws.cloudwatch.EventTarget(
        f"{prefix}-rds-start-target",
        rule=start_rule.name,
        arn=rds_lambda.arn,
        input=json.dumps({"action": "start"}),
    )

    aws.lambda_.Permission(
        f"{prefix}-rds-stop-perm",
        function=rds_lambda.name,
        action="lambda:InvokeFunction",
        principal="events.amazonaws.com",
        source_arn=stop_rule.arn,
    )
    aws.lambda_.Permission(
        f"{prefix}-rds-start-perm",
        function=rds_lambda.name,
        action="lambda:InvokeFunction",
        principal="events.amazonaws.com",
        source_arn=start_rule.arn,
    )

# ── Section E — ECR ───────────────────────────────────────────────────────────
api_repo = aws.ecr.Repository(
    f"{prefix}-api",
    image_scanning_configuration=aws.ecr.RepositoryImageScanningConfigurationArgs(
        scan_on_push=True
    ),
    tags={"Name": f"{prefix}-api"},
)

jupyter_repo = aws.ecr.Repository(
    f"{prefix}-jupyter",
    image_scanning_configuration=aws.ecr.RepositoryImageScanningConfigurationArgs(
        scan_on_push=True
    ),
    tags={"Name": f"{prefix}-jupyter"},
)

# ── Section F — ECS Cluster ───────────────────────────────────────────────────
cluster = aws.ecs.Cluster(f"{prefix}-cluster", tags={"Name": prefix})

# ── Section G — IAM Execution Role ────────────────────────────────────────────
# Grants ECS permission to pull ECR images and write logs, plus access to the
# RDS-managed Secrets Manager secret for DB_PASSWORD.
exec_role = aws.iam.Role(
    f"{prefix}-exec-role",
    assume_role_policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "ecs-tasks.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }),
)

aws.iam.RolePolicyAttachment(
    f"{prefix}-exec-policy",
    role=exec_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
)

# Inline policy allowing ECS to read the RDS-managed master password secret.
aws.iam.RolePolicy(
    f"{prefix}-exec-sm-policy",
    role=exec_role.name,
    policy=db.master_user_secrets.apply(lambda secrets: json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["secretsmanager:GetSecretValue", "kms:Decrypt"],
            "Resource": [secrets[0]["secret_arn"]],
        }],
    })),
)

# ── Section H — CloudWatch Log Groups ─────────────────────────────────────────
api_log_group = aws.cloudwatch.LogGroup(
    f"{prefix}-api-logs",
    name=f"/ecs/{prefix}-api",
    retention_in_days=log_retention_days,
)
jupyter_log_group = aws.cloudwatch.LogGroup(
    f"{prefix}-jupyter-logs",
    name=f"/ecs/{prefix}-jupyter",
    retention_in_days=log_retention_days,
)

# ── Section I — SNS Topic for Alarms ──────────────────────────────────────────
alarm_topic = aws.sns.Topic(f"{prefix}-alarms")
aws.sns.TopicSubscription(
    f"{prefix}-alarm-email",
    topic=alarm_topic.arn,
    protocol="email",
    endpoint=alarm_email,
)

# ── Section J — ALB ───────────────────────────────────────────────────────────
alb = aws.lb.LoadBalancer(
    f"{prefix}-alb",
    internal=False,
    load_balancer_type="application",
    security_groups=[alb_sg.id],
    subnets=[s.id for s in public_subnets],
)

api_tg = aws.lb.TargetGroup(
    f"{prefix}-api-tg",
    port=8000,
    protocol="HTTP",
    target_type="ip",  # required for Fargate
    vpc_id=vpc.id,
    health_check=aws.lb.TargetGroupHealthCheckArgs(path="/docs", port="8000"),
)

jupyter_tg = aws.lb.TargetGroup(
    f"{prefix}-jupyter-tg",
    port=8888,
    protocol="HTTP",
    target_type="ip",
    vpc_id=vpc.id,
    health_check=aws.lb.TargetGroupHealthCheckArgs(path="/", port="8888"),
)

api_listener = aws.lb.Listener(
    f"{prefix}-api-listener",
    load_balancer_arn=alb.arn,
    port=80,
    protocol="HTTP",
    default_actions=[
        aws.lb.ListenerDefaultActionArgs(type="forward", target_group_arn=api_tg.arn)
    ],
)

jupyter_listener = aws.lb.Listener(
    f"{prefix}-jupyter-listener",
    load_balancer_arn=alb.arn,
    port=8888,
    protocol="HTTP",
    default_actions=[
        aws.lb.ListenerDefaultActionArgs(type="forward", target_group_arn=jupyter_tg.arn)
    ],
)


# ── Section K — Container Definition Helper ───────────────────────────────────
def _container_def(
    name: str,
    image_url: str,
    port: int,
    log_group_name: str,
    env_vars: list[dict] | None = None,
    secret_refs: list[dict] | None = None,
) -> list[dict]:
    """Build ECS container definition with optional Secrets Manager refs."""
    return [{
        "name": name,
        "image": f"{image_url}:latest",
        "essential": True,
        "portMappings": [{"containerPort": port, "protocol": "tcp"}],
        "environment": env_vars or [],
        "secrets": secret_refs or [],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": log_group_name,
                "awslogs-region": region,
                "awslogs-stream-prefix": name,
            },
        },
    }]


# ── Section L — Task Definitions ──────────────────────────────────────────────
api_task = aws.ecs.TaskDefinition(
    f"{prefix}-api-task",
    family=f"{prefix}-api",
    cpu=api_cpu,
    memory=api_memory,
    network_mode="awsvpc",
    requires_compatibilities=["FARGATE"],
    execution_role_arn=exec_role.arn,
    container_definitions=pulumi.Output.all(
        api_repo.repository_url,
        db.address,
        db.master_user_secrets,
        api_log_group.name,
    ).apply(lambda a: json.dumps(_container_def(
        "api", a[0], 8000, a[3],
        env_vars=[
            {"name": "DB_HOST",  "value": a[1]},
            {"name": "DB_PORT",  "value": "5432"},
            {"name": "DB_NAME",  "value": "museums"},
            {"name": "DB_USER",  "value": "postgres"},
        ],
        secret_refs=[
            {"name": "DB_PASSWORD", "valueFrom": f"{a[2][0]['secret_arn']}:password::"},
        ],
    ))),
)

jupyter_task = aws.ecs.TaskDefinition(
    f"{prefix}-jupyter-task",
    family=f"{prefix}-jupyter",
    cpu="256",
    memory="512",
    network_mode="awsvpc",
    requires_compatibilities=["FARGATE"],
    execution_role_arn=exec_role.arn,
    container_definitions=pulumi.Output.all(
        jupyter_repo.repository_url,
        jupyter_log_group.name,
        alb.dns_name,
    ).apply(
        lambda a: json.dumps(
            _container_def(
                "jupyter",
                a[0],
                8888,
                a[1],
                env_vars=[{"name": "API_URL", "value": f"http://{a[2]}"}],
            )
        )
    ),
)

# ── Section M — ECS Services ──────────────────────────────────────────────────
api_svc = aws.ecs.Service(
    f"{prefix}-api-svc",
    cluster=cluster.arn,
    task_definition=api_task.arn,
    desired_count=ecs_api_desired,
    launch_type="FARGATE",
    network_configuration=aws.ecs.ServiceNetworkConfigurationArgs(
        subnets=[s.id for s in private_subnets],
        security_groups=[api_sg.id],
        assign_public_ip=False,
    ),
    load_balancers=[
        aws.ecs.ServiceLoadBalancerArgs(
            target_group_arn=api_tg.arn,
            container_name="api",
            container_port=8000,
        )
    ],
    opts=pulumi.ResourceOptions(depends_on=[api_listener]),
)

aws.ecs.Service(
    f"{prefix}-jupyter-svc",
    cluster=cluster.arn,
    task_definition=jupyter_task.arn,
    desired_count=1,
    launch_type="FARGATE",
    network_configuration=aws.ecs.ServiceNetworkConfigurationArgs(
        subnets=[s.id for s in private_subnets],
        security_groups=[jupyter_sg.id],
        assign_public_ip=False,
    ),
    load_balancers=[
        aws.ecs.ServiceLoadBalancerArgs(
            target_group_arn=jupyter_tg.arn,
            container_name="jupyter",
            container_port=8888,
        )
    ],
    opts=pulumi.ResourceOptions(depends_on=[jupyter_listener]),
)

# ── Section N — ECS Auto-scaling ──────────────────────────────────────────────
api_asg_target = aws.appautoscaling.Target(
    f"{prefix}-api-asg",
    max_capacity=ecs_api_max,
    min_capacity=ecs_api_min,
    resource_id=pulumi.Output.all(cluster.name, api_svc.name).apply(
        lambda a: f"service/{a[0]}/{a[1]}"
    ),
    scalable_dimension="ecs:service:DesiredCount",
    service_namespace="ecs",
    opts=pulumi.ResourceOptions(depends_on=[api_svc]),
)

aws.appautoscaling.Policy(
    f"{prefix}-api-cpu-scaling",
    policy_type="TargetTrackingScaling",
    resource_id=api_asg_target.resource_id,
    scalable_dimension=api_asg_target.scalable_dimension,
    service_namespace=api_asg_target.service_namespace,
    target_tracking_scaling_policy_configuration=aws.appautoscaling.PolicyTargetTrackingScalingPolicyConfigurationArgs(
        predefined_metric_specification=aws.appautoscaling.PolicyTargetTrackingScalingPolicyConfigurationPredefinedMetricSpecificationArgs(
            predefined_metric_type="ECSServiceAverageCPUUtilization"
        ),
        target_value=60.0,
        scale_in_cooldown=300,
        scale_out_cooldown=60,
    ),
)

# ── Section O — CloudWatch Alarms ─────────────────────────────────────────────

# 1. 5xx spike on ALB
aws.cloudwatch.MetricAlarm(
    f"{prefix}-5xx-alarm",
    alarm_description="API 5xx error rate spike",
    comparison_operator="GreaterThanThreshold",
    evaluation_periods=2,
    metric_name="HTTPCode_Target_5XX_Count",
    namespace="AWS/ApplicationELB",
    period=300,
    statistic="Sum",
    threshold=5,
    alarm_actions=[alarm_topic.arn],
    dimensions={"LoadBalancer": alb.arn_suffix, "TargetGroup": api_tg.arn_suffix},
    treat_missing_data="notBreaching",
)

# 2. p99 latency on ALB
aws.cloudwatch.MetricAlarm(
    f"{prefix}-p99-latency-alarm",
    alarm_description="API p99 latency > 2s",
    comparison_operator="GreaterThanThreshold",
    evaluation_periods=2,
    metric_name="TargetResponseTime",
    namespace="AWS/ApplicationELB",
    period=300,
    extended_statistic="p99",
    threshold=2.0,
    alarm_actions=[alarm_topic.arn],
    dimensions={"LoadBalancer": alb.arn_suffix, "TargetGroup": api_tg.arn_suffix},
    treat_missing_data="notBreaching",
)

# 3. ECS under-capacity
aws.cloudwatch.MetricAlarm(
    f"{prefix}-ecs-capacity-alarm",
    alarm_description="ECS running tasks below desired",
    comparison_operator="LessThanThreshold",
    evaluation_periods=1,
    metric_name="RunningTaskCount",
    namespace="AWS/ECS",
    period=60,
    statistic="Minimum",
    threshold=ecs_api_min,
    alarm_actions=[alarm_topic.arn],
    dimensions=pulumi.Output.all(cluster.name, api_svc.name).apply(
        lambda a: {"ClusterName": a[0], "ServiceName": a[1]}
    ),
    treat_missing_data="breaching",
)

# 4. RDS CPU utilisation
aws.cloudwatch.MetricAlarm(
    f"{prefix}-rds-cpu-alarm",
    alarm_description="RDS CPU > 90%",
    comparison_operator="GreaterThanThreshold",
    evaluation_periods=2,
    metric_name="CPUUtilization",
    namespace="AWS/RDS",
    period=300,
    statistic="Average",
    threshold=90,
    alarm_actions=[alarm_topic.arn],
    dimensions={"DBInstanceIdentifier": db.identifier},
    treat_missing_data="notBreaching",
)

# ── Section P — Outputs ───────────────────────────────────────────────────────
pulumi.export("api_url", alb.dns_name.apply(lambda d: f"http://{d}"))
pulumi.export("jupyter_url", alb.dns_name.apply(lambda d: f"http://{d}:8888"))
pulumi.export("api_ecr_url", api_repo.repository_url)
pulumi.export("jupyter_ecr_url", jupyter_repo.repository_url)
pulumi.export("rds_endpoint", db.endpoint)
pulumi.export("alarm_topic_arn", alarm_topic.arn)
pulumi.export(
    "db_secret_arn",
    db.master_user_secrets.apply(lambda s: s[0]["secret_arn"]),
)
