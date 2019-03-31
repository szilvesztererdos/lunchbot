import os
import logging
import hashlib
import hmac
from flask import abort, Flask, jsonify, request


app = Flask(__name__)
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('lunchbot')


def verify_slack_request(slack_signature=None, slack_request_timestamp=None, request_body=None):
    slack_signing_secret = os.environ['SLACK_SIGNING_SECRET']

    # form the basestring as stated in the Slack API docs. We need to make a bytestring
    basestring = f"v0:{slack_request_timestamp}:{request_body}".encode('utf-8')

    # make the Signing Secret a bytestring too
    slack_signing_secret = bytes(slack_signing_secret, 'utf-8')

    # create a new HMAC "signature", and return the string presentation
    my_signature = 'v0=' + hmac.new(slack_signing_secret, basestring, hashlib.sha256).hexdigest()

    # Compare the the Slack provided signature to ours.
    # If they are equal, the request should be verified successfully.
    # Log the unsuccessful requests for further analysis
    # (along with another relevant info about the request).
    if hmac.compare_digest(my_signature, slack_signature):
        return True
    else:
        logger.warning(f"Verification failed. my_signature: {my_signature}")
        return False


@app.route('/', methods=['POST'])
def lunchbot():
    # capture the necessary data
    slack_signature = request.headers['X-Slack-Signature']
    slack_request_timestamp = request.headers['X-Slack-Request-Timestamp']

    # verify the request
    if not verify_slack_request(slack_signature, slack_request_timestamp, request.data):
        logger.info('Bad request.')
        response = {
            "statusCode": 400,
            "body": ''
        }
        return response

    return jsonify(
        response_type='in_channel',
        text='hello there',
    )
