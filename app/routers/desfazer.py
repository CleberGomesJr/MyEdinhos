from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import obter_sessao
from app.domain.desfazer import desfazer as desfazer_exclusao
from app.web import resposta_acao

router = APIRouter()


@router.post("/desfazer/{registro_id}")
def desfazer(sessao: Annotated[Session, Depends(obter_sessao)], registro_id: int):
    descricao = desfazer_exclusao(sessao, registro_id)
    if descricao is None:
        sessao.rollback()
        return resposta_acao("Não foi possível desfazer.", tipo="erro", fechar_drawer=False)
    sessao.commit()
    return resposta_acao(f"{descricao} restaurado.", tipo="info", fechar_drawer=False)
