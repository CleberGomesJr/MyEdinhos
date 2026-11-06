from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import obter_sessao

router = APIRouter()


@router.get("/saude")
def saude(sessao: Annotated[Session, Depends(obter_sessao)]) -> dict[str, str]:
    sessao.execute(text("SELECT 1"))
    return {"status": "ok", "banco": "ok"}
