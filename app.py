import os
import logging
import shlex
import json
import pymongo
from urllib.parse import urlparse
from slack import WebClient
from quart import abort, Quart, jsonify, request
import requests
import bson
import re
import asyncio

# TODO: add rate restaurant feature (1 hour later, rating/price/duration)
# TODO: use Google Maps to geocode & get device position in order to take walking time into account
# TODO: major refactor (modules)
# TODO: add a feature to opt-out ("I don't care")
# TODO: if a user doesn't answer after a time limit, it should be opted out
# TODO: show partial results (not good for 1-2 persons)

# globals
app = Quart(__name__)
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("lunchbot")
mongodb_uri = os.environ.get("MONGODB_URI")
try:
    conn = pymongo.MongoClient(mongodb_uri)
    logger.info("MongoDB connection successful.")
except pymongo.errors.ConnectionFailure as e:
    logger.info(f"Could not connect to MongoDB: {e}")

db = conn[urlparse(mongodb_uri).path[1:]]
slack_client = WebClient(os.environ.get('SLACK_ACCESS_TOKEN'), run_async=True)
bot_client = WebClient(os.environ.get('SLACK_BOT_TOKEN'), run_async=True)


""" WEB SERVER ROUTES """


@app.route("/command/lunchbot-add-restaurant", methods=["POST"])
async def handle_add_restaurant():
    try:
        request_values = await request.values
        text = request_values["text"]
        parameters = shlex.split(text)

        if text.startswith("help"):
            response = get_response_for_add_restaurant_help()
        elif len(parameters) < 6:
            response = get_response_for_add_restaurant_few_arguments()
        else:
            try:
                confirmation_answer = {
                    "name": parameters[0],
                    "address": parameters[1],
                    "initial duration": int(parameters[2]),
                    "initial rating": int(parameters[3]),
                    "initial price": int(parameters[4]),
                    "tags": parameters[5:]
                }
            except ValueError:
                response = {
                    "response_type": "ephermal",
                    "text": "Duration, rating and price should be numbers, see `/lunchbot-add-restaurant help` for usage."
                }
            else:
                response = get_response_for_add_restaurant_confirm(confirmation_answer.items())
                db["temp"].insert_one(confirmation_answer)

        return jsonify(response)
    except Exception as e:
        logger.error(f"ERROR: {e}")
        abort(200)


@app.route("/command/lunchbot-list-restaurants", methods=["POST"])
def handle_list_restaurants():
    try:
        restaurants_markdown = generate_restaurants_markdown()
        response = {
            "response_type": "ephermal",
            "blocks": restaurants_markdown
        }

        return jsonify(response)
    except Exception as e:
        logger.error(f"ERROR: {e}")
        abort(200)


@app.route("/command/lunchbot-suggest", methods=["POST"])
async def handle_suggest():
    try:
        request_values = await request.values
        if request_values["text"] == "":
            response = {
                "text": "Please, specify at least one user."
            }
            return jsonify(response)

        # searching for pattern like <@U1234|user>, where we need @U1234
        user_ids_match = re.finditer("@(\S*)\|", request_values["text"])

        response = await slack_api("users.list")
        users = response["members"]
        user_ids = [user["id"] for user in users]

        number_of_mentioned_users = 0

        session_id = ""
        for user_id_match in user_ids_match:
            user_id = user_id_match.group(1)
            if user_id not in user_ids:
                return jsonify({
                    "text": f"{user_id} is not valid. Please, make sure all users are real!"
                })
            else:
                # TODO: cancel session if user is already in another session
                if session_id == "":
                    session_id = db["sessions"].insert_one({
                        "started_user_id": request_values["user_id"],
                        "users": []
                    }).inserted_id
                db["sessions"].update_one(
                    {
                        "_id": session_id
                    },
                    {
                        "$push": {"users": {"user_id": user_id, "finished": False}}
                    }
                )
                asyncio.create_task(
                    start_dm(
                        user_id,
                        get_blocks_for_asking_time_limit(request_values["user_id"])
                    )
                )
            number_of_mentioned_users += 1

        if number_of_mentioned_users == 0:
            return jsonify({
                "text": "I didn't find valid users. Please, make sure all users are real!"
            })
        else:
            return jsonify({
                "text": f"Lunchbot initiated for {number_of_mentioned_users} user(s)."
            })
    except Exception as e:
        logger.error(f"ERROR: {e}")
        abort(200)


@app.route("/actions", methods=["POST"])
async def handle_actions():
    request_values = await request.values
    payload = json.loads(request_values["payload"])

    if payload["actions"][0]["action_id"].startswith("confirm-add-restaurant"):
        # TODO: handle multiple user submission
        restaurant_to_add = db["temp"].find_one_and_delete({})
        if payload["actions"][0]["action_id"] == "confirm-add-restaurant-true":
            db["restaurants"].insert_one(
                restaurant_to_add
            )
            logger.debug(f"Restaurant added to db: {restaurant_to_add}")
            response = {
                "response_type": "ephermal",
                "replace_original": "true",
                "text": f"Successfully added restaurant `{restaurant_to_add['name']}`."
            }
        else:
            response = {
                "response_type": "ephermal",
                "replace_original": "true",
                "text": f"Cancelled to add new restaurant."
            }
    elif payload["actions"][0]["action_id"] == "remove-restaurant":
        restaurant_to_remove = remove_restaurant(payload["actions"][0]["value"])
        response = get_response_for_remove_restaurant(restaurant_to_remove)

    elif payload["actions"][0]["action_id"].startswith("answer-time-limit"):
        # TODO: check if user has valid session
        store_time_limit(payload["user"]["id"], payload["actions"][0]["value"])

        # ask for price limit
        response = get_response_for_answer_time_limit(payload['actions'][0]['value'])

    elif payload["actions"][0]["action_id"].startswith("answer-price-limit"):
        # TODO: check if user has valid session
        store_price_limit(payload["user"]["id"], payload["actions"][0]["value"])

        # ask for tag exclude
        response = get_response_for_answer_price_limit(payload["user"]["id"], payload["actions"][0]["value"])

    elif payload["actions"][0]["action_id"].startswith("answer-tag-exclude"):
        # TODO: check if user has valid session
        store_excluded_tag(payload["user"]["id"], payload["actions"][0]["value"])

        # show updated tag exclude question
        response = get_response_for_answer_tag_exclude(payload["user"]["id"])

    elif payload["actions"][0]["action_id"].startswith("finish-tag-exclude"):
        # TODO: check if user has valid session
        set_user_finished_session(payload["user"]["id"])

        # answer user to wait for others
        response = get_response_for_finish_tag_exclude()

        # check whether all users are finished in this session
        finished_session = get_finished_session_for_user(payload["user"]["id"])

        if finished_session:
            send_suggested_restaurants_to_users(finished_session)
            delete_session_for_user(payload["user"]["id"])

    requests.post(payload["response_url"], json=response)
    return 'OK'


""" SLACK API HELPER FUNCTIONS """


async def slack_api(method, is_bot=False, **kwargs):
    if is_bot:
        api_call = await bot_client.api_call(method, **kwargs)
    else:
        api_call = await slack_client.api_call(method, **kwargs)
    if api_call.get('ok'):
        return api_call
    else:
        raise ValueError('Connection error!', api_call.get('error'), api_call.get('args'))


async def start_dm(user_id, blocks):
    im_response = await slack_api("im.open", is_bot=True, json={"user": user_id})
    args = {
        "channel": im_response["channel"]["id"],
        "blocks": blocks
    }
    await slack_api("chat.postMessage", is_bot=True, json=args)


""" SLACK BLOCK & RESPONSE GENERATOR FUNCTIONS """


def generate_restaurants_markdown():
    restaurants = list(db['restaurants'].find())
    restaurants_markdown = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"There are {len(restaurants)} restaurant(s) in Lunchbot's database:"
            }
        },
        {
            "type": "divider"
        }
    ]
    for i, restaurant in enumerate(restaurants):
        restaurant_name_markdown = f"*{i+1}. {restaurant['name']}*\n"
        restaurant_others_markdown = '\n'.join(get_prettyfied_dict(list(restaurant.items())[2:]))
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
                    "value": str(restaurant["_id"]),
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
    return restaurants_markdown


def get_blocks_for_asking_time_limit(user_id):
    # TODO: generate based on actual restaurant times and db["settings"].find_one({"name": "price_limit_step"})
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Hi! <@{user_id}> called you to have lunch together " +
                    "and I'm helping you to choose where to go for lunch.\n" +
                    "First, please give me how much time you have for lunch."
            }
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "20 min"
                    },
                    "value": "20",
                    "action_id": "answer-time-limit-20"
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "70 min"
                    },
                    "value": "70",
                    "action_id": "answer-time-limit-70"
                }
            ]
        }
    ]


def get_blocks_for_asking_tag_exclude(user_id):
    # getting tags dynamically from restaurants
    tags_aggregated = list(db.restaurants.aggregate([
        {
            "$group": {
                "_id": 0,
                "tags": {
                    "$push": "$tags"
                }
            }
        },
        {"$addFields": {
            "tags": {
                "$reduce": {
                    "input": "$tags",
                    "initialValue": [],
                    "in": {"$setUnion": ["$$value", "$$this"]}
                }
            }
        }}
    ]))[0]["tags"]

    # generate buttons based on aggregated tags
    elements = [
        {
            "type": "button",
            "text": {
                "type": "plain_text",
                "text": "I'm finished"
            },
            "value": "finish",
            "style": "primary",
            "action_id": "finish-tag-exclude"
        }
    ]
    for tag in tags_aggregated:
        # checking whether user already filtered out that tag
        query = db["filters"].find({"user_id": user_id, "tag_exclude": tag})
        element = {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": tag
                },
                "value": tag,
                "action_id": f"answer-tag-exclude-{tag}"
            }
        if query.count() > 0:
            element["style"] = "danger"
        elements.append(
            element
        )

    return [
        {
            "type": "section",
            "text": {
                "type": "plain_text",
                "text": "Okay. Now, choose which restaurant tags do you want to exclude!"
            }
        },
        {
            "type": "actions",
            "elements": elements
        }
    ]


def get_response_for_answer_time_limit(time_limit):
    # TODO: generate based on actual restaurant prices and db["settings"].find_one({"name": "price_limit_step"})
    blocks_layout = [
        {
            "type": "section",
            "text": {
                "type": "plain_text",
                "text": "Okay. Now, choose how much money you have for this lunch!"
            }
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "700 HUF"
                    },
                    "value": "700",
                    "action_id": "answer-price-limit-700"
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "1900 HUF"
                    },
                    "value": "1900",
                    "action_id": "answer-price-limit-1900"
                }
            ]
        }
    ]

    blocks_layout.insert(0, {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"Successfully chosen time limit as `{time_limit} minutes`."
        }
    })
    return {
        "response_type": "ephermal",
        "replace_original": "true",
        "blocks": blocks_layout
    }


def get_response_for_add_restaurant_confirm(confirmation_answer):
    confirmation_answer_pretty = "\n".join(get_prettyfied_dict(confirmation_answer))

    # TODO: solve multi-user submissions as well (sending some id to temp?)
    return {
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


def get_response_for_add_restaurant_help():
    return {
        "response_type": "ephermal",
        "text": """
Usage: `/lunchbot-add-restaurant <"name"> <"address"> <initial duration in minutes> <initial rating 1-5> <initial price> <"tags" separated by spaces>`
(e.g. `/lunchbot-add-restaurant "Suppé" "1065 Budapest, Hajós u. 19." 30 4 1100 hash-house small-place`)
"""
    }


def get_response_for_add_restaurant_few_arguments():
    return {
        "response_type": "ephermal",
        "text": "Too few arguments, see `/lunchbot-add-restaurant help` for usage."
    }


def get_blocks_for_suggested_restaurants(suggested_restaurants):
    suggested_restaurants = list(suggested_restaurants)

    if len(suggested_restaurants) > 0:
        blocks_layout = [{
            "type": "section",
            "text": {
                "type": "plain_text",
                "text": "Based on the inputs here are the suggested restaurants to try."
            }
        }]

        for restaurant in suggested_restaurants:
            blocks_layout.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{restaurant['name']}* ({restaurant['address']}) " +
                            f"{' '.join(map(lambda x: '#'+x, restaurant['tags']))}"
                    }
                }
            )
    else:
        blocks_layout = [{
            "type": "section",
            "text": {
                "type": "plain_text",
                "text": "Based on the inputs we didn't find any restaurants to try."
            }
        }]

    return blocks_layout


def get_response_for_answer_tag_exclude(user_id):
    blocks_layout = get_blocks_for_asking_tag_exclude(user_id)
    return {
        "response_type": "ephermal",
        "replace_original": "true",
        "blocks": blocks_layout
    }


def get_response_for_answer_price_limit(user_id, price_limit):
    blocks_layout = get_blocks_for_asking_tag_exclude(user_id)
    blocks_layout.insert(0, {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"Successfully chosen price limit as `{price_limit} HUF`."
        }
    })

    return {
        "response_type": "ephermal",
        "replace_original": "true",
        "blocks": blocks_layout
    }


def get_response_for_remove_restaurant(restaurant_to_remove):
    blocks_layout = generate_restaurants_markdown()
    blocks_layout.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"Successfully removed restaurant `{restaurant_to_remove['name']}`."
        }
    })

    return {
        "response_type": "ephermal",
        "replace_original": "true",
        "blocks": blocks_layout
    }


def get_response_for_finish_tag_exclude():
    blocks_layout = [{
        "type": "section",
        "text": {
            "type": "plain_text",
            "text": "Thanks for your input. Please wait while others finish answering."
        }
    }]
    return {
        "response_type": "ephermal",
        "replace_original": "true",
        "blocks": blocks_layout
    }


""" DB FUNCTIONS """


def get_suggested_restaurants(user_ids):
    # get filters from db
    time_limit = list(db.filters.aggregate([
        {
            "$match": {
                "user_id": {"$in": user_ids},
                "time_limit": {"$exists": 1}
            }
        },
        {
            "$group": {
                "_id": 0,
                "min_time": {"$min": "$time_limit"}
            }
        }
    ]))[0]["min_time"]

    price_limit = list(db.filters.aggregate([
        {
            "$match": {
                "user_id": {"$in": user_ids},
                "price_limit": {"$exists": 1}
            }
        },
        {
            "$group": {
                "_id": None,
                "min_price": {"$min": "$price_limit"}
            }
        }
    ]))[0]["min_price"]

    excluded_tags_result = list(db.filters.aggregate([
        {
            "$match": {
                "user_id": {"$in": user_ids},
                "tag_exclude": {"$exists": 1}
            }
        },
        {
            "$group": {
                "_id": None,
                "tags": {"$push": "$tag_exclude"}
            }
        }
    ]))
    excluded_tags = excluded_tags_result[0]["tags"] if len(excluded_tags_result) > 0 else []

    # get restaurants from db based on filters
    return list(db["restaurants"].find({
        "initial duration": {"$lte": time_limit},
        "initial price": {"$lte": price_limit},
        "tags": {"$nin": excluded_tags}
    }))


def store_excluded_tag(user_id, excluded_tag):
    # store excluded tag to user in db
    # if the tag is already there, we should delete it (to mimic button switch)
    delete_result = db["filters"].delete_one(
        {
            "user_id": user_id,
            "tag_exclude": excluded_tag
        }
    )
    if delete_result.deleted_count == 0:
        db["filters"].insert_one(
            {
                "user_id": user_id,
                "tag_exclude": excluded_tag
            }
        )


def store_price_limit(user_id, price_limit):
    # store time limit value to user in db, replace if exists
    db["filters"].update_one(
        {
            "user_id": user_id,
            "price_limit": {"$exists": 1}
        },
        {
            "$set": {"price_limit": int(price_limit)}
        },
        upsert=True
    )


def store_time_limit(user_id, time_limit):
    # store time limit value to user in db (replace if exists)
    db["filters"].update_one(
        {
            "user_id": user_id,
            "time_limit": {"$exists": 1}
        },
        {
            "$set": {"time_limit": int(time_limit)}
        },
        upsert=True
    )


def remove_restaurant(restaurant_id):
    restaurant_id_to_remove = restaurant_id
    restaurant_to_remove = db["restaurants"].find_one({"_id": bson.ObjectId(restaurant_id_to_remove)})
    db["restaurants"].delete_one({"_id": bson.ObjectId(restaurant_id_to_remove)})
    logger.debug(f"Restaurant removed from db: {restaurant_to_remove}")
    return restaurant_to_remove


def set_user_finished_session(user_id):
    db["sessions"].update_one(
        {"users.user_id": user_id},
        {"$set": {"users.$.finished": True}}
    )


def get_finished_session_for_user(user_id):
    return db["sessions"].find_one({
        "$nor": [{
                "users": {
                    "$elemMatch": {
                        "user_id": {"$ne": user_id},
                        "finished": {"$ne": True}
                    }
                }
        }]
    })


def delete_session_for_user(user_id):
    db["sessions"].delete_one({"users.user_id": user_id})


""" HELPER FUNCTIONS """


def get_prettyfied_dict(parameters):
    return [f"{k}: {', '.join(v)}" if isinstance(v, list) else f"{k}: {v}" for k, v in parameters]


def send_suggested_restaurants_to_users(finished_session):
    user_ids = [user["user_id"] for user in finished_session["users"]]
    suggested_restaurants = get_suggested_restaurants(user_ids)
    for user_id in user_ids:
        asyncio.create_task(
            start_dm(
                user_id,
                get_blocks_for_suggested_restaurants(suggested_restaurants)
            )
        )
