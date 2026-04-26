from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from museums.data.db import City, Museum, get_db, init_db
from museums.data.wikipedia import fetch_city_populations, fetch_museum_list
from museums.ml.regression import run_regression


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Museums API", version="1.0.0", lifespan=lifespan)


@app.post(
    "/ingest",
    summary="Fetch museums + city populations from Wikipedia/Wikidata and persist",
)
def ingest(session: Session = Depends(get_db)):
    museums = fetch_museum_list()
    city_names = list({m["city"] for m in museums})
    populations = fetch_city_populations(city_names)

    # Bulk-fetch existing rows to avoid N+1 queries
    existing_cities = {
        c.name: c for c in session.query(City).filter(City.name.in_(city_names)).all()
    }
    existing_museums = {
        m.name: m
        for m in session.query(Museum)
        .filter(Museum.name.in_([m["name"] for m in museums]))
        .all()
    }

    city_objs: dict[str, City] = {}
    for m in museums:
        city_name = m["city"]
        city = existing_cities.get(city_name)
        if not city:
            city = City(
                name=city_name,
                country=m["country"],
                population=populations.get(city_name),
            )
            session.add(city)
        else:
            city.population = populations.get(city_name, city.population)
        city_objs[city_name] = city

    session.flush()

    for m in museums:
        museum = existing_museums.get(m["name"])
        if not museum:
            session.add(
                Museum(
                    name=m["name"],
                    city=city_objs[m["city"]],
                    annual_visitors=m["visitors"],
                )
            )
        else:
            museum.annual_visitors = m["visitors"]

    session.commit()
    return {"ingested_museums": len(museums), "cities": len(city_names)}


@app.get("/museums")
def list_museums(session: Session = Depends(get_db)):
    rows = session.query(Museum).join(City).all()
    return [
        {
            "id": m.id,
            "name": m.name,
            "city": m.city.name,
            "country": m.city.country,
            "annual_visitors": m.annual_visitors,
            "city_population": m.city.population,
        }
        for m in rows
    ]


@app.get("/cities")
def list_cities(session: Session = Depends(get_db)):
    return [
        {"id": c.id, "name": c.name, "country": c.country, "population": c.population}
        for c in session.query(City).all()
    ]


@app.get("/regression")
def regression(session: Session = Depends(get_db)):
    pairs = (
        session.query(Museum, City).join(City).filter(City.population.isnot(None)).all()
    )
    if len(pairs) < 2:
        raise HTTPException(
            status_code=422, detail="Not enough data — run POST /ingest first."
        )

    data = [
        {
            "city": city.name,
            "population": city.population,
            "visitors": museum.annual_visitors,
        }
        for museum, city in pairs
    ]
    result = run_regression(data)
    return {
        "slope": result.slope,
        "intercept": result.intercept,
        "r_squared": result.r_squared,
        "predictions": result.predictions,
    }
