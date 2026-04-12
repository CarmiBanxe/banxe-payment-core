"""Tests for ComplianceScreening — I-01 (sanctions first), I-02 (Category A)."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.compliance_bridge.screening import (
    _CATEGORY_A,
    ComplianceScreening,
    ScreeningDecision,
    ScreeningRequest,
)


def make_request(country: str = "GB", amount: int = 1000) -> ScreeningRequest:
    return ScreeningRequest(
        customer_id="cust_001",
        counterparty_country=country,
        amount_minor=amount,
        currency="GBP",
        reference="txn_001",
    )


def _mock_client(json_response: dict) -> MagicMock:
    """Build a mock httpx.AsyncClient context manager."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.json.return_value = json_response
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)
    return mock_client


class TestCategoryABlocking:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("country", ["RU", "BY", "IR", "KP", "CU", "MM", "AF"])
    async def test_blocks_category_a_without_api_call(self, country):
        """I-02: Category A → immediate BLOCK, no HTTP call."""
        screening = ComplianceScreening()
        result = await screening.screen(make_request(country=country))
        assert result.decision == ScreeningDecision.BLOCK
        assert country in result.reason
        assert result.risk_score == 100

    @pytest.mark.asyncio
    async def test_category_a_case_insensitive(self):
        """I-02: lowercase country code should also block."""
        screening = ComplianceScreening()
        result = await screening.screen(make_request(country="ru"))
        assert result.decision == ScreeningDecision.BLOCK

    def test_category_a_set_contents(self):
        assert "RU" in _CATEGORY_A
        assert "BY" in _CATEGORY_A
        assert "GB" not in _CATEGORY_A
        assert "US" not in _CATEGORY_A


class TestComplianceAPIResponses:
    @pytest.mark.asyncio
    async def test_pass_on_clean_transaction(self):
        screening = ComplianceScreening(base_url="http://localhost:8093")
        with patch(
            "httpx.AsyncClient",
            return_value=_mock_client(
                {
                    "flagged": False,
                    "risk_score": 10,
                    "recommended_action": "APPROVE",
                    "rules_triggered": [],
                }
            ),
        ):
            result = await screening.screen(make_request())
        assert result.decision == ScreeningDecision.PASS
        assert result.risk_score == 10

    @pytest.mark.asyncio
    async def test_block_on_flagged_true(self):
        screening = ComplianceScreening(base_url="http://localhost:8093")
        with patch(
            "httpx.AsyncClient",
            return_value=_mock_client(
                {
                    "flagged": True,
                    "risk_score": 85,
                    "recommended_action": "SAR",
                    "rules_triggered": [{"name": "high_risk"}],
                }
            ),
        ):
            result = await screening.screen(make_request())
        assert result.decision == ScreeningDecision.BLOCK
        assert result.risk_score == 85

    @pytest.mark.asyncio
    async def test_block_on_sar_action(self):
        screening = ComplianceScreening(base_url="http://localhost:8093")
        with patch(
            "httpx.AsyncClient",
            return_value=_mock_client(
                {
                    "flagged": False,
                    "risk_score": 70,
                    "recommended_action": "SAR",
                    "rules_triggered": [],
                }
            ),
        ):
            result = await screening.screen(make_request())
        assert result.decision == ScreeningDecision.BLOCK

    @pytest.mark.asyncio
    async def test_block_on_reject_action(self):
        screening = ComplianceScreening(base_url="http://localhost:8093")
        with patch(
            "httpx.AsyncClient",
            return_value=_mock_client(
                {
                    "flagged": False,
                    "risk_score": 60,
                    "recommended_action": "REJECT",
                    "rules_triggered": [],
                }
            ),
        ):
            result = await screening.screen(make_request())
        assert result.decision == ScreeningDecision.BLOCK

    @pytest.mark.asyncio
    async def test_review_on_hold_action(self):
        """HOLD → REVIEW (HITL)."""
        screening = ComplianceScreening(base_url="http://localhost:8093")
        with patch(
            "httpx.AsyncClient",
            return_value=_mock_client(
                {
                    "flagged": False,
                    "risk_score": 55,
                    "recommended_action": "HOLD",
                    "rules_triggered": [],
                }
            ),
        ):
            result = await screening.screen(make_request())
        assert result.decision == ScreeningDecision.REVIEW

    @pytest.mark.asyncio
    async def test_block_reason_from_rules_triggered(self):
        """If rules_triggered present, reason comes from first rule name."""
        screening = ComplianceScreening(base_url="http://localhost:8093")
        with patch(
            "httpx.AsyncClient",
            return_value=_mock_client(
                {
                    "flagged": True,
                    "risk_score": 90,
                    "recommended_action": "SAR",
                    "rules_triggered": [{"name": "sanctions_hit"}],
                }
            ),
        ):
            result = await screening.screen(make_request())
        assert result.decision == ScreeningDecision.BLOCK
        assert result.reason == "sanctions_hit"

    @pytest.mark.asyncio
    async def test_block_reason_fallback_when_no_rules(self):
        """No rules_triggered → reason is 'flagged'."""
        screening = ComplianceScreening(base_url="http://localhost:8093")
        with patch(
            "httpx.AsyncClient",
            return_value=_mock_client(
                {
                    "flagged": True,
                    "risk_score": 80,
                    "recommended_action": "SAR",
                    "rules_triggered": [],
                }
            ),
        ):
            result = await screening.screen(make_request())
        assert result.reason == "flagged"


class TestFailClosed:
    @pytest.mark.asyncio
    async def test_blocks_when_api_unreachable(self):
        """I-01: fail-closed — if API unreachable, BLOCK (not PASS)."""
        screening = ComplianceScreening(base_url="http://localhost:8093")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await screening.screen(make_request())

        assert result.decision == ScreeningDecision.BLOCK
        assert "unreachable" in result.reason.lower()

    def test_default_url_is_8093(self):
        screening = ComplianceScreening()
        assert "8093" in screening._base_url

    def test_custom_url(self):
        screening = ComplianceScreening(base_url="http://custom:9000")
        assert "custom" in screening._base_url
