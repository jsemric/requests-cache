import boto3
from boto3.resources.base import ServiceResource
from botocore.exceptions import ClientError

from . import BaseCache, BaseStorage, get_valid_kwargs


class DynamoDbCache(BaseCache):
    """DynamoDB cache backend

    Args:
        table_name: DynamoDb table name
        namespace: Name of DynamoDb hash map
        connection: :boto3:`DynamoDb Resource <services/dynamodb.html#DynamoDB.ServiceResource>`
            object to use instead of creating a new one
        kwargs: Additional keyword arguments for :py:meth:`~boto3.session.Session.resource`
    """

    def __init__(self, table_name: str = 'http_cache', connection: ServiceResource = None, **kwargs):
        super().__init__(**kwargs)
        self.responses = DynamoDbDict(table_name, 'responses', connection=connection, **kwargs)
        self.redirects = DynamoDbDict(table_name, 'redirects', connection=self.responses.connection, **kwargs)


class DynamoDbDict(BaseStorage):
    """A dictionary-like interface for DynamoDB key-value store

    **Note:** The actual key name on the dynamodb server will be ``namespace``:``table_name``

    In order to deal with how dynamodb stores data/keys,
    everything, i.e. keys and data, must be pickled.

    Args:
        table_name: DynamoDb table name
        namespace: Name of DynamoDb hash map
        connection: :boto3:`DynamoDb Resource <services/dynamodb.html#DynamoDB.ServiceResource>`
            object to use instead of creating a new one
        kwargs: Additional keyword arguments for :py:meth:`~boto3.session.Session.resource`
    """

    def __init__(
        self,
        table_name,
        namespace='http_cache',
        connection=None,
        read_capacity_units=1,
        write_capacity_units=1,
        **kwargs,
    ):
        super().__init__(**kwargs)
        connection_kwargs = get_valid_kwargs(boto3.Session, kwargs, extras=['endpoint_url'])
        self.connection = connection or boto3.resource('dynamodb', **connection_kwargs)
        self._self_key = namespace

        try:
            self.connection.create_table(
                AttributeDefinitions=[
                    {
                        'AttributeName': 'namespace',
                        'AttributeType': 'S',
                    },
                    {
                        'AttributeName': 'key',
                        'AttributeType': 'S',
                    },
                ],
                TableName=table_name,
                KeySchema=[
                    {'AttributeName': 'namespace', 'KeyType': 'HASH'},
                    {'AttributeName': 'key', 'KeyType': 'RANGE'},
                ],
                ProvisionedThroughput={
                    'ReadCapacityUnits': read_capacity_units,
                    'WriteCapacityUnits': write_capacity_units,
                },
            )
        except ClientError:
            pass
        self._table = self.connection.Table(table_name)
        self._table.wait_until_exists()

    def __getitem__(self, key):
        composite_key = {'namespace': self._self_key, 'key': str(key)}
        result = self._table.get_item(Key=composite_key)
        if 'Item' not in result:
            raise KeyError
        return self.deserialize(result['Item']['value'].value)

    def __setitem__(self, key, item):
        item = {'namespace': self._self_key, 'key': str(key), 'value': self.serialize(item)}
        self._table.put_item(Item=item)

    def __delitem__(self, key):
        composite_key = {'namespace': self._self_key, 'key': str(key)}
        response = self._table.delete_item(Key=composite_key, ReturnValues='ALL_OLD')
        if 'Attributes' not in response:
            raise KeyError

    def __len__(self):
        return self.__count_table()

    def __iter__(self):
        response = self.__scan_table()
        for v in response['Items']:
            yield self.deserialize(v['value'].value)

    def clear(self):
        response = self.__scan_table()
        for v in response['Items']:
            composite_key = {'namespace': v['namespace'], 'key': v['key']}
            self._table.delete_item(Key=composite_key)

    def __str__(self):
        return str(dict(self.items()))

    def __scan_table(self):
        expression_attribute_values = {':Namespace': self._self_key}
        expression_attribute_names = {'#N': 'namespace'}
        key_condition_expression = '#N = :Namespace'
        return self._table.query(
            ExpressionAttributeValues=expression_attribute_values,
            ExpressionAttributeNames=expression_attribute_names,
            KeyConditionExpression=key_condition_expression,
        )

    def __count_table(self):
        expression_attribute_values = {':Namespace': self._self_key}
        expression_attribute_names = {'#N': 'namespace'}
        key_condition_expression = '#N = :Namespace'
        return self._table.query(
            Select='COUNT',
            ExpressionAttributeValues=expression_attribute_values,
            ExpressionAttributeNames=expression_attribute_names,
            KeyConditionExpression=key_condition_expression,
        )['Count']
