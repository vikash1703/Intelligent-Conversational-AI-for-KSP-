from pydantic import BaseModel
from typing import Optional

class ReportRequest(BaseModel):
    crime_no: str
    report_content: str
    author: Optional[str] = "AI Assistant"