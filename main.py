import json
import logging
import re

import requests
import requests_toolbelt.adapters.appengine
from flask import Flask, jsonify
from flask import redirect
from flask import render_template
from flask import request
from google.appengine.api.app_identity import get_application_id
from google.appengine.ext import ndb

from models import Installation

app = Flask(__name__)
app.config['DEBUG'] = True

requests_toolbelt.adapters.appengine.monkeypatch()
CARD_PATTERN = re.compile("^/[cC][aA][rR][dD](.*)")

MTG_API_URL = "https://api.magicthegathering.io/v1/cards"
GATHERER_URL = "http://gatherer.wizards.com/Pages/Card/Details.aspx?multiverseid=%d"


def strip_slash_command(card_message):
    return CARD_PATTERN.search(card_message).group(1).strip()


@app.route('/')
def root():
    """Return a friendly HTTP greeting."""
    return 'Magic is for nerds!'


@app.errorhandler(404)
def page_not_found(e):
    """Return a custom 404 error."""
    return 'Sorry, nothing at this URL.', 404


@app.route('/capabilities', methods=['GET'])
def capabilities():
    """Returns the capabilities of this Hipchat integration.  This assumes that your Hipchat integration key matches
    your Google project ID.  This may not be the case - modify capabilities.json accordingly.
    """
    return jsonify(json.loads(render_template('capabilities.json', project_id=get_application_id()))), 200


@app.route('/installed', methods=['POST'])
def install_callback():
    """Hipchat will hit this endpoint when the integration has been installed by the end user."""
    installation_dict = json.loads(request.data)
    capabilities_url = installation_dict['capabilitiesUrl']

    logging.info("Getting capabilities from %s", capabilities_url)
    cap_dict = requests.get(capabilities_url).json()

    Installation(
        room_id=installation_dict['roomId'],
        group_id=installation_dict['groupId'],
        oauth_secret=installation_dict['oauthSecret'],
        token_url=cap_dict['capabilities']['oauth2Provider']['tokenUrl'],
        api_url=cap_dict['capabilities']['hipchatApiProvider']['url'],
        key=ndb.Key(Installation, installation_dict['oauthId'])
    ).put()

    return '', 201


@app.route('/uninstalled', methods=['GET'])
def uninstall_callback():
    """Hipchat will hit this endpoint when the integration has been uninstalled by the end user."""
    redirect_url = request.args.get('redirect_url')
    installable_url = request.args.get('installable_url')

    logging.info("Getting installable info from %s", installable_url)
    installation_dict = requests.get(installable_url).json()

    oauth_id = installation_dict['oauthId']

    logging.info("Deleting installation with oauth ID %s", oauth_id)
    installation_key = ndb.Key(Installation, oauth_id)
    installation_key.delete()

    logging.info("Redirecting to %s", redirect_url)
    return redirect(redirect_url, 302)


@app.route('/card', methods=['POST'])
def card():
    """Webhook endpoint.  Looks up the card specified and sends it back to the hipchat room.
    """
    request_dict = json.loads(request.data)
    oauth_id = request_dict['oauth_client_id']

    installation = Installation.get_by_id(oauth_id)

    if not installation:
        logging.warning("Installation with oauth id %s not found", oauth_id)
        return '', 204

    full_message = request_dict['item']['message']['message']
    card_name = strip_slash_command(full_message)

    logging.info("Looking up card %s.", card_name)

    cards_dict = requests.get(MTG_API_URL, params={"name": '"%s"' % card_name}).json()
    cards_list = cards_dict["cards"]

    if not cards_list:
        logging.error("No card found with name %s.", card_name)
        payload = {
            "color": "red",
            "message": "Sorry, I couldn't find a card called %s." % card_name,
            "message_format": "text"
        }
        installation.send_notification(payload)
        return '', 204

    card_dict = cards_list[0]
    official_card_name = card_dict["name"]
    card_id = card_dict["id"]
    card_text = card_dict.get("text", "")
    multiverse_id = card_dict["multiverseid"]
    image_url = card_dict["imageUrl"]

    payload = {
        "color": "green",
        "message": official_card_name,
        "message_format": "text",
        "card": {
            "style": "link",
            "url": GATHERER_URL % multiverse_id,
            "id": card_id,
            "title": official_card_name,
            "description": card_text,
            "thumbnail": {
                "url": image_url
            }
        }
    }

    logging.info(payload)
    installation.send_notification(payload)

    return '', 204
