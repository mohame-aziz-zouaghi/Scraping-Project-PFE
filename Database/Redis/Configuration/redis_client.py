import redis


class RedisClient:

    _instance = None

    def __new__(cls):

        if cls._instance is None:

            cls._instance = super().__new__(cls)

            cls._instance.client = redis.Redis(
                host="localhost",
                port=6379,
                db=0,
                decode_responses=True
            )

        return cls._instance

    def get_client(self):
        return self.client