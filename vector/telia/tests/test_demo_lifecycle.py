"""
Pytest integration test for Telia Vector Edge Demo lifecycle.
Verifies port recycling, REST API, CORS headers, claim token validation, and audit trail.
"""

from vector.telia.scripts.test_demo_iterations import DemoIterationTester


def test_telia_vector_demo_lifecycle():
    """Verify that the Telia Vector edge daemon and sovereign hub complete 3 clean iterations."""
    tester = DemoIterationTester(fast_mode=True)
    for i in range(1, 4):
        res = tester.run_single_iteration(i)
        assert res["status"] == "PASSED"
        assert res["agent_rss_mb"] < 40.0, f"RSS {res['agent_rss_mb']}MB exceeded 40MB budget"
        assert "valid_claim_flow" in res["checks"]
        assert "canonical_manifest" in res["checks"]
        assert "structured_audit_logs" in res["checks"]
