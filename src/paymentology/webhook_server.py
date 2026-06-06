"""FastAPI webhook server for Paymentology Remote API callbacks."""
import logging

from fastapi import FastAPI, Request, Response

from .remote_handler import RemoteAPIHandler

logger = logging.getLogger(__name__)

app = FastAPI(title="BANXE Paymentology Webhook", version="0.1.0")

_handler: RemoteAPIHandler | None = None


def init_webhook(handler: RemoteAPIHandler):
    global _handler
    _handler = handler


@app.post("/paymentology/webhook")
async def paymentology_webhook(request: Request):
    body = await request.body()
    xml_text = body.decode("utf-8")
    logger.info("Paymentology webhook received %d bytes", len(xml_text))
    if _handler is None:
        return Response(content="<methodResponse><params><param><value><struct>"
            "<member><name>resultCode</name><value><int>0</int></value></member>"
            "</struct></value></param></params></methodResponse>",
            media_type="text/xml", status_code=200)
    result_xml = await _handler.dispatch(xml_text)
    return Response(content=result_xml, media_type="text/xml", status_code=200)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "paymentology-webhook"}
