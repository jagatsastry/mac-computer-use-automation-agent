from dataclasses import dataclass
from typing import Optional


@dataclass
class ActuatorResult:
    success: bool
    output: str = ""
    error: Optional[str] = None

    def to_dict(self) -> dict:
        result = {"success": self.success, "output": self.output}
        if self.error:
            result["error"] = self.error
        return result
