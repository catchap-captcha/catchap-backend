"""InstitutionPicker: 기관 검색 + 시도>시군구>동 드릴다운 (공개, 무인증)."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Institution

router = APIRouter(prefix="/institutions", tags=["institutions"])


def _row(o: Institution) -> dict:
    return {
        "id": o.id,
        "name": o.name,
        "type": o.inst_type,
        "sido": o.sido,
        "sigungu": o.sigungu,
        "dong": o.dong,
        "road_address": o.road_address,
        "organization_id": o.organization_id,
    }


@router.get("/search")
def search_institutions(
    q: str | None = Query(default=None),
    sido: str | None = Query(default=None),
    sigungu: str | None = Query(default=None),
    dong: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    query = db.query(Institution)
    qq = (q or "").strip()
    if qq:
        if len(qq) < 2:
            return []
        like = f"%{qq}%"
        query = query.filter(
            (Institution.name.like(like)) | (Institution.road_address.like(like))
        )
    else:
        if sido:
            query = query.filter(Institution.sido == sido)
        if sigungu:
            query = query.filter(Institution.sigungu == sigungu)
        if dong:
            query = query.filter(Institution.dong == dong)
        if not (sido or sigungu or dong):
            return []
    return [_row(o) for o in query.order_by(Institution.name).limit(50).all()]


@router.get("/regions")
def regions(
    sido: str | None = Query(default=None),
    sigungu: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """sido 미지정→시도 목록, sido 지정→시군구 목록, sigungu까지 지정→동 목록."""
    if sido and sigungu:
        col = Institution.dong
        query = db.query(col).filter(Institution.sido == sido, Institution.sigungu == sigungu)
    elif sido:
        col = Institution.sigungu
        query = db.query(col).filter(Institution.sido == sido)
    else:
        col = Institution.sido
        query = db.query(col)
    rows = query.distinct().order_by(col).all()
    return [r[0] for r in rows]
