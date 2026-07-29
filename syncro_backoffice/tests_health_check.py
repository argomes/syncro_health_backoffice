from unittest.mock import patch

from django.db.utils import OperationalError
from django.test import Client, TestCase


class HealthCheckLivenessTest(TestCase):
    """
    /health/ é liveness puro: não deve tocar banco/redis, sempre 200 se o
    processo Django está de pé. Usado pelo HEALTHCHECK do Dockerfile.
    """

    def test_health_check_returns_200_without_touching_database(self):
        response = self.client.get('/health/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')


class HealthCheckReadinessTest(TestCase):
    """
    TASK-BO-07: /health/ready/ é o healthcheckPath do Railway — só deve
    responder 200 se o banco estiver de fato acessível (SELECT 1 real),
    e 503 (não um 200 genérico) se o banco estiver inacessível.
    """

    def test_health_check_ready_returns_200_when_database_is_reachable(self):
        response = self.client.get('/health/ready/')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['database'], 'ok')

    def test_health_check_ready_returns_503_when_database_is_unreachable(self):
        with patch('syncro_backoffice.urls.connection') as mock_connection:
            mock_connection.cursor.side_effect = OperationalError('connection refused')
            response = Client().get('/health/ready/')
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body['status'], 'unhealthy')
        self.assertEqual(body['database'], 'unreachable')
