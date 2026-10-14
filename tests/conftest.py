import pytest


@pytest.fixture(autouse=True)
def mock_vertex_auth(mocker):
    """Regression tests never use real credentials or make Google requests."""
    mocker.patch(
        "gfjproxy.providers.vertex.vertex_request_auth",
        return_value=(
            "https://aiplatform.googleapis.com/v1/projects/test-project/locations/global/publishers/google/models/gemini-2.5-flash:generateContent",
            {"Authorization": "Bearer test-access-token"},
        ),
    )
