"""Library loan operations: borrowing and returning books."""
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Loan, MemberTier
from app.schemas import LoanCreate, LoanOut, LoanStatus
from app.services import books, members

# Maximum concurrent unreturned loans per tier (None = unlimited).
TIER_LOAN_LIMIT: Dict[str, Optional[int]] = {
    MemberTier.APPRENTICE.value: 1,
    MemberTier.ADEPT.value: 3,
    MemberTier.MASTER.value: 5,
    MemberTier.SUPREME.value: None,
}

LOAN_PERIOD = timedelta(days=14)
LATE_FEE_PER_DAY_CENTS = 25


def loan_status(loan: Loan, now: datetime) -> LoanStatus:
    """``returned`` if returned; else ``overdue`` if now > due_at; else ``active``."""
    if loan.returned_at is not None:
        return "returned"
    # Strictly after the due date: a loan sitting exactly on due_at is still active.
    return "overdue" if now > loan.due_at else "active"


def to_loan_out(loan: Loan, now: datetime) -> LoanOut:
    """Serialize a loan, computing its status at read time."""
    return LoanOut(
        id=loan.id,
        member_id=loan.member_id,
        book_id=loan.book_id,
        borrowed_at=loan.borrowed_at,
        due_at=loan.due_at,
        returned_at=loan.returned_at,
        late_fee_cents=loan.late_fee_cents,
        status=loan_status(loan, now),
    )


def calculate_late_fee(due_at: datetime, returned_at: datetime, price_cents: int) -> int:
    """25 cents per started day late (any partial day counts), capped at the book's price; 0 if not late."""
    if returned_at <= due_at:
        return 0
    overdue_by = returned_at - due_at
    # Any remainder past a whole day starts the next one, so a second late costs a full day.
    days_late = overdue_by.days + (1 if overdue_by.seconds or overdue_by.microseconds else 0)
    return min(days_late * LATE_FEE_PER_DAY_CENTS, price_cents)


def create_loan(db: Session, data: LoanCreate, now: datetime) -> LoanOut:
    """Borrow a book for 14 days.

    Checks, in order:
    1. 404 member not found; 404 book not found
    2. 403 book restricted and member tier below master
    3. 409 member has any overdue loan
    4. 409 member already has an unreturned loan of this book
    5. 409 member is at their tier's loan limit
    6. 409 book is out of stock
    On success: borrowed_at = now, due_at = now + 14 days, returned_at None,
    late_fee_cents 0, and stock is decremented by one.
    """
    member = members.get_member(db, data.member_id)
    book = books.get_book(db, data.book_id)

    if book.restricted:
        members.ensure_can_access_restricted(member)

    open_loans = list(
        db.scalars(
            select(Loan).where(Loan.member_id == member.id, Loan.returned_at.is_(None))
        )
    )
    if any(now > loan.due_at for loan in open_loans):
        raise HTTPException(status_code=409, detail="Return your overdue books before borrowing again")
    if any(loan.book_id == book.id for loan in open_loans):
        raise HTTPException(status_code=409, detail="You already have this book on loan")
    limit = TIER_LOAN_LIMIT[member.tier]
    if limit is not None and len(open_loans) >= limit:
        raise HTTPException(status_code=409, detail=f"Tier '{member.tier}' may hold {limit} loans at a time")
    if book.stock == 0:
        raise HTTPException(status_code=409, detail="No copies available to borrow")

    loan = Loan(
        member_id=member.id,
        book_id=book.id,
        borrowed_at=now,
        due_at=now + LOAN_PERIOD,
        returned_at=None,
        late_fee_cents=0,
    )
    book.stock -= 1
    db.add(loan)
    db.commit()
    db.refresh(loan)
    return to_loan_out(loan, now)


def get_loan(db: Session, loan_id: int, now: datetime) -> LoanOut:
    """Return a loan by id, or raise 404."""
    return to_loan_out(load_loan(db, loan_id), now)


def load_loan(db: Session, loan_id: int) -> Loan:
    """Fetch the ORM row, or raise 404. Used where the loan is about to be modified."""
    loan = db.get(Loan, loan_id)
    if loan is None:
        raise HTTPException(status_code=404, detail="Loan not found")
    return loan


def return_loan(db: Session, loan_id: int, now: datetime) -> LoanOut:
    """Return a borrowed book.

    Rules: 404 if missing; 409 if already returned. Sets returned_at = now, restores one copy
    of stock and charges a late fee (see ``calculate_late_fee``).
    """
    loan = load_loan(db, loan_id)
    if loan.returned_at is not None:
        raise HTTPException(status_code=409, detail="This loan has already been returned")

    loan.returned_at = now
    # Priced at return time, so a book that was repriced while on loan is charged at today's value.
    loan.late_fee_cents = calculate_late_fee(loan.due_at, now, loan.book.price_cents)
    loan.book.stock += 1
    db.commit()
    db.refresh(loan)
    return to_loan_out(loan, now)


def list_member_loans(
    db: Session, member_id: int, now: datetime, status: Optional[LoanStatus] = None
) -> List[LoanOut]:
    """A member's loans ordered by id, optionally filtered by computed status; 404 if member missing."""
    members.get_member(db, member_id)
    loans = db.scalars(select(Loan).where(Loan.member_id == member_id).order_by(Loan.id)).all()
    # Status is derived from now rather than stored, so filtering happens after serialization.
    serialized = [to_loan_out(loan, now) for loan in loans]
    return [loan for loan in serialized if status is None or loan.status == status]
