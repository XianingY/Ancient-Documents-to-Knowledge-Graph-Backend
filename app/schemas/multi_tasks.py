
from typing import List
from pydantic import BaseModel

class CreateMultiTaskRequest(BaseModel):
    structured_result_ids: List[int]

class CreateMultiTaskByImagesRequest(BaseModel):
    image_ids: List[int]
