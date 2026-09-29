"""Candidate: unified structured object produced by info-collection tools."""
from pydantic import BaseModel, Field


class GeoLocation(BaseModel):
    lat: float
    lng: float


class Candidate(BaseModel):
    """A single option to be scored (restaurant, attraction, school, ...)."""

    id: str
    name: str
    category: str = "restaurant"
    location: GeoLocation | None = None
    distance_m: int | None = Field(default=None, description="distance from user in meters")
    price_per_person: float | None = Field(default=None, description="avg price per person (CNY)")
    rating: float | None = Field(default=None, ge=0, le=5)
    wait_min: int | None = Field(default=None, description="estimated queue minutes")
    tags: list[str] = Field(default_factory=list, description="e.g. 川菜/辣/清淡/安静")
    crowd_index: float | None = Field(default=None, description="crowdedness 0~100 (lower = quieter)")
    raw_data_ref: str | None = Field(default=None, description="offloaded full data reference")
