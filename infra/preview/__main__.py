"""Museums preview — shared ECS infrastructure for per-PR preview deployments.

These resources are provisioned ONCE and shared across all PR preview tasks.
The per-PR lifecycle (run-task / stop-task) is managed by GitHub Actions.

Deploy:
    cd infra/preview
    pulumi stack init preview
    pulumi up
"""

import json
import pulumi
import pulumi_aws as aws

prefix = "museums-preview"
region = aws.get_region().name

# ── Reuse default VPC + public subnets ───────────────────────────────────────
default_vpc = aws.ec2.get_vpc(default=True)
public_subnets = aws.ec2.get_subnets(filters=[
    aws.ec2.GetSubnetsFilterArgs(name="vpc-id", values=[default_vpc.id]),
    aws.ec2.GetSubnetsFilterArgs(name="map-public-ip-on-launch", values=["true"]),
])

# ── Security group: allow inbound :8000 (preview API) ────────────────────────
preview_sg = aws.ec2.SecurityGroup(
    f"{prefix}-sg",
    vpc_id=default_vpc.id,
    description="Preview ECS tasks — inbound port 8000",
    ingress=[
        aws.ec2.SecurityGroupIngressArgs(
            from_port=8000, to_port=8000, protocol="tcp",
            cidr_blocks=["0.0.0.0/0"],
            description="Preview API access",
        )
    ],
    egress=[
        aws.ec2.SecurityGroupEgressArgs(
            from_port=0, to_port=0, protocol="-1", cidr_blocks=["0.0.0.0/0"]
        )
    ],
    tags={"Name": prefix},
)

# ── ECS cluster (shared by all preview tasks) ─────────────────────────────────
cluster = aws.ecs.Cluster(f"{prefix}-cluster", tags={"Name": prefix})

# ── IAM execution role ────────────────────────────────────────────────────────
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

# ── ECR repository ────────────────────────────────────────────────────────────
ecr_repo = aws.ecr.Repository(
    f"{prefix}-api",
    image_scanning_configuration=aws.ecr.RepositoryImageScanningConfigurationArgs(
        scan_on_push=True
    ),
    tags={"Name": f"{prefix}-api"},
)

# ── CloudWatch log group (1-day retention — previews are ephemeral) ───────────
log_group = aws.cloudwatch.LogGroup(
    f"{prefix}-logs",
    name=f"/ecs/{prefix}-api",
    retention_in_days=1,
)

# ── Task definition (placeholder — GitHub Actions re-registers with PR image) ─
task_def = aws.ecs.TaskDefinition(
    f"{prefix}-api-task",
    family=f"{prefix}-api",
    cpu="256",
    memory="512",
    network_mode="awsvpc",
    requires_compatibilities=["FARGATE"],
    execution_role_arn=exec_role.arn,
    container_definitions=pulumi.Output.all(
        ecr_repo.repository_url, log_group.name
    ).apply(lambda a: json.dumps([{
        "name": "api",
        "image": f"{a[0]}:latest",
        "essential": True,
        "portMappings": [{"containerPort": 8000, "protocol": "tcp"}],
        "environment": [
            # SQLite: ephemeral, data lives only for the PR task lifetime
            {"name": "DATABASE_URL", "value": "sqlite:///./data/museums.db"},
        ],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": a[1],
                "awslogs-region": region,
                "awslogs-stream-prefix": "api",
            },
        },
    }])),
)

# ── Outputs (consumed by preview.yml GitHub Actions workflow) ─────────────────
pulumi.export("cluster_name", cluster.name)
pulumi.export("task_definition_family", task_def.family)
pulumi.export("security_group_id", preview_sg.id)
pulumi.export("subnet_ids", public_subnets.ids.apply(lambda ids: ",".join(ids)))
pulumi.export("ecr_url", ecr_repo.repository_url)
