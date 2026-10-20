import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import main


@pytest_asyncio.fixture
async def client(tmp_path):
    db_path = tmp_path / "wallets.db"
    database_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"

    previous_engine = main.engine
    previous_session_factory = main.async_session_factory
    previous_database_url = main.DATABASE_URL
    previous_lock = main.wallet_lock

    main.DATABASE_URL = database_url
    main.engine = main.create_async_engine(database_url)
    main.async_session_factory = main.async_sessionmaker(
        bind=main.engine,
        expire_on_commit=False,
    )
    main.wallet_lock = asyncio.Lock()

    async with main.engine.begin() as connection:
        await connection.run_sync(main.Base.metadata.create_all)

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    async with main.engine.begin() as connection:
        await connection.run_sync(main.Base.metadata.drop_all)
    await main.engine.dispose()

    main.engine = previous_engine
    main.async_session_factory = previous_session_factory
    main.DATABASE_URL = previous_database_url
    main.wallet_lock = previous_lock


@pytest.mark.asyncio
async def test_get_wallet_returns_404_for_missing_wallet(client):
    response = await client.get("/api/v1/wallets/missing-wallet")

    assert response.status_code == 404
    assert response.json()["detail"] == "Wallet not found"


@pytest.mark.asyncio
async def test_deposit_creates_wallet_and_sets_balance(client):
    response = await client.post(
        "/api/v1/wallets/new-wallet/operation",
        json={"operation_type": "DEPOSIT", "amount": 100.0},
    )

    assert response.status_code == 200
    assert response.json() == {"wallet_uuid": "new-wallet", "balance": 100.0}


@pytest.mark.asyncio
async def test_withdraw_reduces_balance(client):
    await client.post(
        "/api/v1/wallets/wallet-1/operation",
        json={"operation_type": "DEPOSIT", "amount": 100.0},
    )

    response = await client.post(
        "/api/v1/wallets/wallet-1/operation",
        json={"operation_type": "WITHDRAW", "amount": 30.0},
    )

    assert response.status_code == 200
    assert response.json() == {"wallet_uuid": "wallet-1", "balance": 70.0}


@pytest.mark.asyncio
async def test_withdraw_with_insufficient_funds_returns_400(client):
    await client.post(
        "/api/v1/wallets/wallet-2/operation",
        json={"operation_type": "DEPOSIT", "amount": 20.0},
    )

    response = await client.post(
        "/api/v1/wallets/wallet-2/operation",
        json={"operation_type": "WITHDRAW", "amount": 50.0},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Insufficient funds"


@pytest.mark.asyncio
async def test_parallel_deposits_for_same_wallet_are_serialized(client):
    wallet_uuid = "wallet-3"

    async def make_deposit(amount: float):
        return await client.post(
            f"/api/v1/wallets/{wallet_uuid}/operation",
            json={"operation_type": "DEPOSIT", "amount": amount},
        )

    responses = await asyncio.gather(*(make_deposit(10.0) for _ in range(20)))

    assert all(response.status_code == 200 for response in responses)

    balance_response = await client.get(f"/api/v1/wallets/{wallet_uuid}")
    assert balance_response.status_code == 200
    assert balance_response.json()["balance"] == 200.0
