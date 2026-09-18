"""Two real HTTP integrations. No API key required for non-commercial use."""
import httpx


async def find_city(name: str) -> dict | None:
    """Geocoding converts a city name into latitude/longitude."""
    async with httpx.AsyncClient(timeout=12) as client:
        response = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": name, "count": 1, "language": "en"},
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        return results[0] if results else None


async def fetch_forecast(place: dict) -> dict:
    """Return two daily forecasts in the city's own timezone, in Celsius."""
    async with httpx.AsyncClient(timeout=12) as client:
        response = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["latitude"], "longitude": place["longitude"],
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto", "forecast_days": 2,
                "temperature_unit": "celsius",
            },
        )
        response.raise_for_status()
        return response.json()
