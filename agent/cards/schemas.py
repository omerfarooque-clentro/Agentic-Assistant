from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field


class ConditionEnum(str, Enum):
    CLEAR = "clear"
    PARTLY_CLOUDY = "partly_cloudy"
    CLOUDY = "cloudy"
    FOG = "fog"
    DRIZZLE = "drizzle"
    RAIN = "rain"
    THUNDERSTORM = "thunderstorm"
    SNOW = "snow"


class WeatherCurrent(BaseModel):
    temp: float
    unit: str = "°C"
    condition: ConditionEnum
    feels_like: float
    humidity: int
    wind: str
    rain_chance: int
    pressure: float | None = None
    uv_index: float | None = None
    is_day: int | bool | None = 1


class WeatherHourly(BaseModel):
    time: str
    temp: float
    condition: ConditionEnum
    rain_chance: int = 0
    feels_like: float | None = None
    uv_index: float | None = None
    is_day: int | bool | None = 1


class WeatherDay(BaseModel):
    date: str
    label: str
    condition: ConditionEnum
    low: float
    high: float
    rain_chance: int
    sunrise: str | None = None
    sunset: str | None = None
    uv_index_max: float | None = None
    feels_like: float | None = None
    pressure: float | None = None
    humidity: int | None = None
    wind: str | None = None
    hourly: list[WeatherHourly] = Field(default_factory=list)


class WeatherData(BaseModel):
    city: str
    country: str
    updated_at: str
    current: WeatherCurrent
    days: list[WeatherDay] = Field(default_factory=list)
    hourly: list[WeatherHourly] = Field(default_factory=list)


class EmailItem(BaseModel):
    id: str
    sender_name: str
    sender_email: str
    subject: str
    snippet: str
    received_at: str
    unread: bool = False
    thread_url: str | None = None


class EmailListData(BaseModel):
    query: str
    total: int
    unread: int
    items: list[EmailItem] = Field(default_factory=list)


class SlackMentionItem(BaseModel):
    channel: str
    sender: str
    text: str
    ts: str = ""
    permalink: str | None = None


class SlackMentionsData(BaseModel):
    items: list[SlackMentionItem] = Field(default_factory=list)


class SheetViewData(BaseModel):
    title: str
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    url: str | None = None


class SheetUpdateData(BaseModel):
    title: str
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    highlight_row_index: int = 0
    row_number: int | None = None
    url: str | None = None


class CalendarEventItem(BaseModel):
    title: str
    start: str
    end: str
    attendees: list[str] = Field(default_factory=list)
    meet_url: str | None = None
    html_link: str | None = None
    status: str | None = None


class CalendarEventData(BaseModel):
    events: list[CalendarEventItem] = Field(default_factory=list)


class SearchPoint(BaseModel):
    tag: str
    text: str


class SearchSource(BaseModel):
    domain: str
    url: str


class SearchSummaryData(BaseModel):
    title: str
    source_count: int
    points: list[SearchPoint] = Field(default_factory=list)
    sources: list[SearchSource] = Field(default_factory=list)


class PresentationConfig(BaseModel):
    order: Literal["text_first", "card_first"] = "text_first"
    max_rows: int = 3


class ResultCardEnvelope(BaseModel):
    kind: Literal["result"] = "result"
    type: str
    v: int = 1
    presentation: PresentationConfig = Field(default_factory=PresentationConfig)
    data: dict[str, Any]
