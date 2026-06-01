"""Parse trajectory data from simulation results."""

import json
from pathlib import Path
from typing import Any


class TrajectoryPoint:
    """Single point in vehicle trajectory."""
    
    def __init__(self, time: float, edge_id: str, lat: float, lon: float, speed_kmh: float):
        self.time = time
        self.edge_id = edge_id
        self.lat = lat
        self.lon = lon
        self.speed_kmh = speed_kmh
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "time": self.time,
            "edge_id": self.edge_id,
            "lat": self.lat,
            "lon": self.lon,
            "speed_kmh": self.speed_kmh,
        }


class EmergencyTrajectory:
    """Trajectory of emergency vehicle."""
    
    def __init__(self, mode: str, parameter_id: str, repeat_id: str):
        self.mode = mode
        self.parameter_id = parameter_id
        self.repeat_id = repeat_id
        self.points: list[TrajectoryPoint] = []
        self.start_time: float = 0.0
        self.end_time: float = 0.0
        self.total_travel_time_sec: float = 0.0
    
    def add_point(self, point: TrajectoryPoint) -> None:
        """Add a point to trajectory."""
        self.points.append(point)
        if not self.points[:-1]:  # First point
            self.start_time = point.time
        self.end_time = point.time
        self.total_travel_time_sec = self.end_time - self.start_time
    
    def to_geojson_feature(self) -> dict[str, Any]:
        """Convert to GeoJSON feature (LineString)."""
        if len(self.points) < 2:
            return {}
        
        coords = [[p.lon, p.lat] for p in self.points]
        
        return {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coords,
            },
            "properties": {
                "mode": self.mode,
                "parameter_id": self.parameter_id,
                "repeat_id": self.repeat_id,
                "travel_time_sec": round(self.total_travel_time_sec, 2),
                "point_count": len(self.points),
                "start_time": self.start_time,
                "end_time": self.end_time,
            },
        }
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "mode": self.mode,
            "parameter_id": self.parameter_id,
            "repeat_id": self.repeat_id,
            "points": [p.to_dict() for p in self.points],
            "travel_time_sec": round(self.total_travel_time_sec, 2),
        }


def load_trajectory_geojson(path: Path) -> dict[str, Any]:
    """Load GeoJSON trajectory file."""
    if not path.exists():
        return {"type": "FeatureCollection", "features": []}
    
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_trajectory_geojson(path: Path, trajectories: list[EmergencyTrajectory]) -> None:
    """Save trajectories as GeoJSON."""
    features = [t.to_geojson_feature() for t in trajectories]
    feature_collection = {
        "type": "FeatureCollection",
        "features": [f for f in features if f],
    }
    
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(feature_collection, f, indent=2, ensure_ascii=False)
