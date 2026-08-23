"""
Weather Lookup Module for Amigo Voice Assistant.
Keyless weather service (wttr.in) with offline error handling. Zero API keys.
"""

import requests


def get_weather_data(city=""):
    """
    Fetches rich structured weather data without API keys using wttr.in.
    Returns a dict suitable for UI widgets and conversational responses.
    """
    city_clean = city.strip() if city else ""
    url = f"https://wttr.in/{city_clean}?format=j1" if city_clean else "https://wttr.in/?format=j1"

    try:
        response = requests.get(url, timeout=5, headers={"User-Agent": "AmigoAI/1.0"})
        if response.status_code == 200:
            data = response.json()
            current = data.get("current_condition", [{}])[0]
            nearest = data.get("nearest_area", [{}])[0]
            
            resolved_city = city_clean.capitalize()
            if not resolved_city:
                area_names = nearest.get("areaName", [{}])
                resolved_city = area_names[0].get("value", "Local") if area_names else "Local"

            temp_c = current.get("temp_C", "0")
            temp_f = current.get("temp_F", "32")
            feels_like_c = current.get("FeelsLikeC", temp_c)
            humidity = current.get("humidity", "0")
            wind_kmph = current.get("windspeedKmph", "0")
            uv_index = current.get("uvIndex", "0")
            weather_desc = (
                current.get("weatherDesc", [{}])[0].get("value", "Clear")
                if current.get("weatherDesc")
                else "Clear"
            )
            weather_code = current.get("weatherCode", "113")

            # Map weather code to visual icon token
            icon_type = "sunny"
            code_int = int(weather_code) if str(weather_code).isdigit() else 113
            if code_int in (116, 119, 122):
                icon_type = "partly-cloudy" if code_int == 116 else "cloudy"
            elif code_int in (143, 248, 260):
                icon_type = "fog"
            elif code_int in (200, 386, 389, 392, 395):
                icon_type = "thunderstorm"
            elif code_int in (179, 182, 185, 227, 230, 317, 320, 323, 326, 329, 332, 335, 338, 350, 368, 371):
                icon_type = "snow"
            elif code_int in (176, 263, 266, 281, 284, 293, 296, 299, 302, 305, 308, 311, 314, 353, 356, 359):
                icon_type = "rain"

            return {
                "success": True,
                "city": resolved_city,
                "temp_c": temp_c,
                "temp_f": temp_f,
                "feels_like_c": feels_like_c,
                "humidity": humidity,
                "wind_kmph": wind_kmph,
                "uv_index": uv_index,
                "condition": weather_desc,
                "weather_code": weather_code,
                "icon_type": icon_type,
            }
    except Exception as e:
        return {
            "success": False,
            "city": city_clean or "Local",
            "error": str(e),
            "temp_c": "--",
            "condition": "Offline",
            "icon_type": "sunny",
        }

    return {
        "success": False,
        "city": city_clean or "Local",
        "temp_c": "--",
        "condition": "Unavailable",
        "icon_type": "sunny",
    }


def get_weather(city):
    """
    Fetches weather for a city without any API keys using open weather service wttr.in.
    """
    if not city:
        return "Please specify a city to check the weather."

    data = get_weather_data(city)
    if data.get("success"):
        return (
            f"The weather in {data['city']} is {data['condition']}. "
            f"The temperature is {data['temp_c']} degrees Celsius with {data['humidity']} percent humidity."
        )
    return f"Could not retrieve weather details for {city} right now."


def weather_command(query):
    """Parse city from query string and return weather info string."""
    city = ""
    if "in" in query:
        city = query.split("in")[-1].strip()
    elif "of" in query:
        city = query.split("of")[-1].strip()
    else:
        city = (
            query.replace("weather", "")
            .replace("temperature", "")
            .replace("climate", "")
            .strip()
        )

    return get_weather(city)

