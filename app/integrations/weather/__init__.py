"""Weather provider integrations."""

from app.integrations.weather.base import WeatherLocation, WeatherProvider
from app.integrations.weather.open_meteo import OpenMeteoWeatherProvider

__all__ = ["OpenMeteoWeatherProvider", "WeatherLocation", "WeatherProvider"]
