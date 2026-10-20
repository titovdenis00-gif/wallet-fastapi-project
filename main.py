import asyncio
import os
from enum import Enum
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

app = FastAPI(title="Кошелек, тестовое задание")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./wallets.db",
)
engine = create_async_engine(DATABASE_URL)
async_session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
wallet_lock = asyncio.Lock()


class Base(DeclarativeBase):
    pass


class WalletModel(Base):
    __tablename__ = "wallets"

    wallet_uuid: Mapped[str] = mapped_column(primary_key=True)
    balance: Mapped[float] = mapped_column(default=0.0)


async def init_db() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    async with async_session_factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


@app.on_event("startup")
async def startup() -> None:
    await init_db()


class OperationType(str, Enum):
    DEPOSIT = "DEPOSIT"
    WITHDRAW = "WITHDRAW"


class WalletOperationRequest(BaseModel):
    operation_type: OperationType
    amount: float = Field(
        gt=0,
        description="Сумма операции должна быть больше нуля",
    )


@app.get(
    "/api/v1/wallets/{wallet_uuid}",
    tags=["Кошелек"],
    summary="Получить баланс кошелька",
    description="Возвращает текущий баланс указанного кошелька",
)
async def get_wallet(wallet_uuid: str, session: SessionDep):
    wallet = await session.get(WalletModel, wallet_uuid)
    if wallet is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet not found",
        )

    return {"wallet_uuid": wallet.wallet_uuid, "balance": wallet.balance}


@app.post(
    "/api/v1/wallets/{wallet_uuid}/operation",
    tags=["Кошелек"],
    summary="Изменить баланс кошелька",
    description="Выполняет пополнение или списание средств",
)
async def wallet_operation(
    wallet_uuid: str,
    payload: WalletOperationRequest,
    session: SessionDep,
):
    async with wallet_lock:
        wallet = await session.get(WalletModel, wallet_uuid)

        if payload.operation_type == OperationType.DEPOSIT:
            if wallet is None:
                wallet = WalletModel(wallet_uuid=wallet_uuid, balance=payload.amount)
                session.add(wallet)
            else:
                wallet.balance += payload.amount
        else:
            if wallet is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Wallet not found",
                )

            if payload.amount > wallet.balance:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Insufficient funds",
                )
            wallet.balance -= payload.amount

        await session.commit()
        await session.refresh(wallet)

    return {"wallet_uuid": wallet.wallet_uuid, "balance": wallet.balance}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
