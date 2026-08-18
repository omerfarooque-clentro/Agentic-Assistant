"""
ASGI config for PERSONAL_OPS_AGENT project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.1/howto/deployment/asgi/
"""

import os
import sys
import asyncio

from django.core.asgi import get_asgi_application

if sys.platform == "win32":
	asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

django_application = get_asgi_application()

from agent.graph import close_checkpointer, setup_checkpointer


async def application(scope, receive, send):
	if scope["type"] != "lifespan":
		await django_application(scope, receive, send)
		return

	while True:
		message = await receive()

		if message["type"] == "lifespan.startup":
			try:
				await setup_checkpointer()
			except Exception as error:
				await send({
					"type": "lifespan.startup.failed",
					"message": str(error),
				})
				return

			await send({"type": "lifespan.startup.complete"})

		elif message["type"] == "lifespan.shutdown":
			await close_checkpointer()
			await send({"type": "lifespan.shutdown.complete"})
			return
