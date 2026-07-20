"""T-37 / T-38: the gateway's generated OpenAPI document is a real contract.

T-37 (BB-114) locks that the error envelope (``ErrorResponse`` — ``{code, message,
details}``) is declared in the OpenAPI document generated from the *live* app object,
and that a real error response the running app emits actually validates against that
declared schema (not merely that a same-named key exists somewhere).

T-38 (BB-115) locks that ``GET /og/graph`` publishes a typed ``$ref``'d 200 response
(``OgGraphResponse``) instead of an empty/``unknown`` schema, so the frontend has
something real to derive types from.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hachi.gateway.control_plane import GatewayControlPlane
from hachi.gateway.fastapi_app import ErrorResponse, create_gateway_app


def test_openapi_declares_the_error_envelope() -> None:
    """T-37 (BB-114): the {code, message, details} envelope the gateway returns
    must be in the spec generated from the live app, and a real error response
    must validate against the declared schema.
    """
    app = create_gateway_app(GatewayControlPlane(), auth_token="secret-token")
    spec = app.openapi()

    schemas = spec["components"]["schemas"]
    assert "ErrorResponse" in schemas, "ErrorResponse missing from components.schemas"
    error_schema = schemas["ErrorResponse"]
    assert set(error_schema["properties"]) >= {"code", "message", "details"}
    assert set(error_schema.get("required", [])) == {"code", "message"}

    # And at least one protected route must actually reference it for 401.
    # Pinned to a route confirmed present:
    # `grep '"/sessions/{session_id}/interrupt"' openapi/hachi-openapi.json`
    responses = spec["paths"]["/sessions/{session_id}/interrupt"]["post"]["responses"]
    assert "401" in responses, "/sessions/{session_id}/interrupt has no declared 401 response"
    ref = responses["401"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/ErrorResponse")

    # Not merely "a key exists": trigger the exact 401 the spec just promised, on
    # the exact route, and validate the REAL runtime body against the REAL
    # ErrorResponse model. A generated client trusts this contract; if the spec
    # and the runtime ever disagree, this must fail.
    with TestClient(app) as client:
        response = client.post("/sessions/does-not-exist/interrupt")
    assert response.status_code == 401
    body = response.json()

    # Raises pydantic.ValidationError (failing the test) if the runtime body does
    # not actually conform to the schema the spec declares for this response.
    validated = ErrorResponse.model_validate(body)
    assert validated.code == "unauthorized"
    assert isinstance(validated.message, str) and validated.message

    # The envelope is exactly {code, message, details} -- the runtime must not
    # emit fields the declared schema doesn't know about either.
    assert set(body) <= set(error_schema["properties"])


def test_og_graph_route_declares_a_response_schema() -> None:
    """T-38 (BB-115): /og/graph must publish a real schema so the frontend can
    derive its types, instead of an empty/``unknown`` 200 response.
    """
    app = create_gateway_app(GatewayControlPlane())
    spec = app.openapi()

    schema = spec["paths"]["/og/graph"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert "$ref" in schema, "GET /og/graph has an untyped 200 response"

    name = schema["$ref"].rsplit("/", 1)[-1]
    props = spec["components"]["schemas"][name]["properties"]
    assert set(props) >= {"nodes", "count"}

    # Prove the runtime -- including the no-handler fallback every test
    # environment hits (no `get_og_graph` callable wired) -- actually satisfies
    # the declared model rather than 500ing on response validation.
    from hachi.gateway.fastapi_app import OgGraphResponse

    with TestClient(app) as client:
        response = client.get("/og/graph")
    assert response.status_code == 200
    OgGraphResponse.model_validate(response.json())
