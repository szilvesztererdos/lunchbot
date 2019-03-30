from slackclient import SlackClient
import logging
import os
import time


def main():

    # 0.1 second delay between reading from firehose
    READ_WEBSOCKET_DELAY = 0.1

    if slack_client.rtm_connect():
        logger.info('QuestionBot connected and running!')
        while slack_client.server.connected is True:
            print(slack_client.rtm_read())
            time.sleep(READ_WEBSOCKET_DELAY)
    else:
        logger.info('Connection failed. Invalid Slack token or bot ID?')


# globals
slack_client = SlackClient(os.environ.get('SLACK_BOT_TOKEN'))
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('lunchbot')

if __name__ == "__main__":
    main()
