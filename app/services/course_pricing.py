"""코스 가격 계산의 단일 정본."""

from datetime import datetime

from app.models import Course


def effective_course_price(course: Course, *, now: datetime | None = None) -> int:
    """현재 적용할 원화 가격. 잘못된 구데이터가 있어도 음수 결제는 만들지 않는다."""
    now = now or datetime.now()
    list_price = max(0, int(course.price or 0))
    sale_price = course.sale_price
    if (
        sale_price is not None
        and 0 <= int(sale_price) <= list_price
        and (course.sale_ends_at is None or course.sale_ends_at > now)
    ):
        return int(sale_price)
    return list_price
