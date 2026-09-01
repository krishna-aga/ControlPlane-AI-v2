from fastapi import APIRouter

router = APIRouter()

# TODO: POST /policies/org, POST /policies/agent
# Each runs control_plane.resolve()/compile(), persists the layer + resulting
# bundle via db, and returns the compiled bundle plus any clamp events.
