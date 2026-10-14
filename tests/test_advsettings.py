import pytest

from gfjproxy.handlers import handle_chat_message
from gfjproxy.models import JaiMessage, JaiRequest
from gfjproxy.utils import ResponseHelper
from gfjproxy.xuiduser import XUID, LocalUserStorage, UserSettings

ADVSETTINGS = {
    "temperature": "temperature",
    "frequency_penalty": "frequencyPenalty",
    "repetition_penalty": "presencePenalty",
    "top_k": "topK",
    "top_p": "topP",
}


@pytest.mark.parametrize(
    "advsettings",
    [
        {},
        {"temperature": 0.69},
        {"temperature": 0.42, "top_k": 95},
        {"temperature": 1.23, "top_k": 25, "top_p": 0.75},
        {"temperature": 2.0, "frequency_penalty": 1.5, "repetition_penalty": -1.5},
        {
            "temperature": 1.0,
            "frequency_penalty": 0.5,
            "repetition_penalty": -0.5,
            "top_k": 75,
            "top_p": 0.25,
        },
        {"temperature": 0.0, "frequency_penalty": 0.0, "top_p": 0.0},
    ],
)
def test_advset_this(mocker, advsettings):
    mock_call = mocker.patch("gfjproxy.providers.vertex.http_client.post")
    mock_response = mocker.Mock()
    mock_response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "Bot response"}]}}],
    }
    mock_call.return_value = mock_response
    user = UserSettings(LocalUserStorage(), XUID("test", "user"))
    jai_req = JaiRequest(
        models={"google": "gemini-2.5-flash"},
        quiet=True,
        quiet_commands=True,
    )
    for key, value in advsettings.items():
        setattr(jai_req, key, value)
    jai_req.messages = [
        JaiMessage.parse(
            {
                "role": "user",
                "content": "".join(f"//advset_{key} this\n" for key in advsettings),
            }
        )
    ]
    result = handle_chat_message(user, jai_req, ResponseHelper())
    assert result.status == 200
    mock_call.assert_called_once()
    config = mock_call.call_args.kwargs["json"]["generationConfig"]
    for key, api_key in ADVSETTINGS.items():
        if key in advsettings:
            assert config[api_key] == pytest.approx(advsettings[key])
        else:
            assert api_key not in config
