import os
import logging
import shlex
import json
import pymongo
from urllib.parse import urlparse
from slackeventsapi import SlackEventAdapter
from flask import abort, Flask, jsonify, request
import requests
import bson

# globals
app = Flask(__name__)
slack_events_adapter = SlackEventAdapter(os.environ["SLACK_SIGNING_SECRET"], "/slack/events", app)
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("lunchbot")
mongodb_uri = os.environ.get("MONGODB_URI")
try:
    conn = pymongo.MongoClient(mongodb_uri)
    logger.info("MongoDB connection successful.")
except pymongo.errors.ConnectionFailure as e:
    logger.info(f"Could not connect to MongoDB: {e}")

db = conn[urlparse(mongodb_uri).path[1:]]


@app.route("/command/lunchbot-add-restaurant", methods=["POST"])
def handle_add_restaurant():
    try:
        text = request.values["text"]
        parameters = shlex.split(text)

        if text.startswith("help"):
            response = get_response_for_add_restaurant_help()
        elif len(parameters) < 6:
            response = get_response_for_add_restaurant_few_arguments()
        else:
            response = get_response_for_add_restaurant_confirm(parameters)

        return jsonify(response)
    except Exception as e:
        logger.error(f"ERROR: {e}")
        abort(200)


@app.route("/command/lunchbot-list-restaurants", methods=["POST"])
def handle_list_restaurants():
    try:
        restaurant_entries = list(db['restaurants'].find())
        restaurants_markdown = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"There are {len(restaurant_entries)} restaurant(s) in Lunchbot's database:"
                }
            },
            {
                "type": "divider"
            }
        ]
        for i, restaurant_entry in enumerate(restaurant_entries):
            restaurant = restaurant_entry["value"]
            restaurant_name_markdown = f"*{i+1}. {restaurant['name']}*\n"
            restaurant_others_markdown = '\n'.join(f"{key}: {value}" for key, value in list(restaurant.items())[2:])
            restaurants_markdown.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": restaurant_name_markdown + restaurant_others_markdown
                }
            })
            restaurants_markdown.append({
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Remove restaurant"
                        },
                        "action_id": "remove-restaurant",
                        "value": str(restaurant_entry["_id"]),
                        "style": "danger",
                        "confirm": {
                            "title": {
                                "type": "plain_text",
                                "text": "Are you sure?"
                            },
                            "confirm": {
                                "type": "plain_text",
                                "text": "Yes"
                            },
                            "deny": {
                                "type": "plain_text",
                                "text": "No"
                            }
                        }
                    }
                ]
            })
        response = {
            "response_type": "ephermal",
            "blocks": restaurants_markdown
        }

        return jsonify(response)
    except Exception as e:
        logger.error(f"ERROR: {e}")
        abort(200)


@app.route("/actions", methods=["POST"])
def handle_actions():
    payload = json.loads(request.values["payload"])

    if payload["actions"][0]["action_id"].startswith("confirm-add-restaurant"):
        restaurant_to_add = db["temp"].find_one_and_delete({"name": "temp_restaurant"})
        if payload["actions"][0]["action_id"] == "confirm-add-restaurant-true":
            db["restaurants"].insert_one(
                restaurant_to_add
            )
            logger.debug(f"Restaurant added to db: {restaurant_to_add}")
            response = {
                "response_type": "ephermal",
                "replace_original": "true",
                "text": f"Successfully added restaurant `{restaurant_to_add['value']['name']}`."
            }
        else:
            response = {
                "response_type": "ephermal",
                "replace_original": "true",
                "text": f"Cancelled to add new restaurant."
            }
    # TODO: make it nicer: after removal the list should be displayed again
    elif payload["actions"][0]["action_id"] == "remove-restaurant":
        restaurant_id_to_remove = payload["actions"][0]["value"]
        restaurant_to_remove = db["restaurants"].find_one({"_id": bson.ObjectId(restaurant_id_to_remove)})
        db["restaurants"].delete_one({"_id": bson.ObjectId(restaurant_id_to_remove)})
        logger.debug(f"Restaurant removed from db: {restaurant_to_remove}")
        response = {
            "response_type": "ephermal",
            "replace_original": "true",
            "text": f"Successfully removed restaurant `{restaurant_to_remove['value']['name']}`."
        }

    requests.post(payload["response_url"], json=response)
    return 'OK'


def get_response_for_add_restaurant_confirm(parameters):
    try:
        confirmation_answer = {
            "name": parameters[0],
            "address": parameters[1],
            "initial duration": int(parameters[2]),
            "initial rating": int(parameters[3]),
            "initial price": int(parameters[4]),
            "tags": ', '.join(parameters[5:])
        }
    except ValueError:
        response = {
            "response_type": "ephermal",
            "text": "Duration, rating and price should be numbers, see `/lunchbot-add-restaurant help` for usage."
        }

        return response

    confirmation_answer_pretty = json.dumps(
        confirmation_answer, ensure_ascii=False, indent=0
    )[1:-1].replace('"', '')

    db['temp'].insert_one({
        "name": "temp_restaurant",
        "value": confirmation_answer
    })

    # TODO: solve multi-user submissions as well (sending some id to temp?)
    response = {
        "response_type": "ephermal",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "plain_text",
                    "text":
                        f"Do you want to add a restaurant with the following parameters?\n{confirmation_answer_pretty}"
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Add restaurant"
                        },
                        "style": "primary",
                        "action_id": "confirm-add-restaurant-true"
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Cancel"
                        },
                        "style": "danger",
                        "action_id": "confirm-add-restaurant-false"
                    }
                ]
            }
        ]
    }
    return response


def get_response_for_add_restaurant_help():
    response = {
        "response_type": "ephermal",
        "text": """
Usage: `/lunchbot-add-restaurant <"name"> <"address"> <initial duration in minutes> <initial rating 1-5> <initial price> <"tags" separated by spaces>`
(e.g. `/lunchbot-add-restaurant "Suppé" "1065 Budapest, Hajós u. 19." 30 4 1100 hash-house small-place`)
"""
    }

    return response


def get_response_for_add_restaurant_few_arguments():
    response = {
        "response_type": "ephermal",
        "text": "Too few arguments, see `/lunchbot-add-restaurant help` for usage."
    }

    return response