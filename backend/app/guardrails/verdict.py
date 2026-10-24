from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["allowed", "off_topic", "prompt_injection", "unsafe"]


class Verdict(BaseModel):
    category: Category
    reason: str = Field(description="One short sentence explaining the category.")
    source: Literal["rules", "classifier", "default"] = "default"

    @property
    def blocked(self) -> bool:
        return self.category != "allowed"


ALLOWED = Verdict(category="allowed", reason="Passed all checks.")
