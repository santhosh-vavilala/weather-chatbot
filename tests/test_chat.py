"""Tests exercise the real HTTP endpoint and graph; only external APIs are mocked."""
import httpx
import pytest
from fastapi.testclient import TestClient
import app as server
import weather_api
from graph import build_graph


@pytest.fixture
def client(monkeypatch):
    server.graph = build_graph()
    server.locks.clear()
    async def city(name):
        if name.lower() == 'notacity':
            return None
        return {'name': name, 'country': 'India', 'latitude': 17.38, 'longitude': 78.48}
    async def forecast(place):
        return {'timezone': 'Asia/Kolkata', 'daily': {
            'time': ['2026-09-18', '2026-09-19'],
            'temperature_2m_min': [22, 23], 'temperature_2m_max': [30, 31],
            'precipitation_probability_max': [20, 80]}}
    monkeypatch.setattr(weather_api, 'find_city', city)
    monkeypatch.setattr(weather_api, 'fetch_forecast', forecast)
    with TestClient(server.app) as c:
        yield c


def turn(client, message, thread=None):
    r = client.post('/api/chat', json={'message': message, 'thread_id': thread})
    assert r.status_code == 200, r.text
    return r.json()


def test_city_clarification_and_memory(client):
    a = turn(client, 'Will it rain tomorrow?')
    assert 'ask_city' in a['nodes_visited']
    b = turn(client, 'Hyderabad, India', a['thread_id'])
    assert b['state']['day'] == 1
    assert '80%' in b['reply']
    assert b['state']['message_count'] == 4
    assert b['nodes_visited'] == ['understand', 'resolve_city', 'get_weather', 'answer', 'remember_reply']


def test_followup_and_new_city(client):
    a = turn(client, 'Weather in Hyderabad today')
    b = turn(client, 'What about tomorrow?', a['thread_id'])
    assert b['state']['city'] == 'Hyderabad'
    assert '80%' in b['reply']
    c = turn(client, 'What about London?', a['thread_id'])
    assert c['state']['city'] == 'London'
    assert c['state']['day'] == 0


def test_isolation(client):
    turn(client, 'Weather in Hyderabad')
    b = turn(client, 'What about tomorrow?')
    assert b['state']['city'] == ''
    assert 'ask_city' in b['nodes_visited']


def test_city_not_found_clears_previous(client):
    a = turn(client, 'Weather in Hyderabad')
    b = turn(client, 'Weather in notacity', a['thread_id'])
    assert "couldn't find" in b['reply']
    assert b['state']['city'] == ''
    assert 'get_weather' not in b['nodes_visited']


def test_api_failure_does_not_reuse_old_weather(client, monkeypatch):
    a = turn(client, 'Weather in Hyderabad')
    async def fail(place):
        raise httpx.ReadTimeout('timeout')
    monkeypatch.setattr(weather_api, 'fetch_forecast', fail)
    b = turn(client, 'What about tomorrow?', a['thread_id'])
    assert 'usable forecast' in b['reply']
    assert '20%' not in b['reply']


def test_null_weather(client, monkeypatch):
    async def bad(place):
        return {'daily': {'time': [None]}}
    monkeypatch.setattr(weather_api, 'fetch_forecast', bad)
    assert 'usable forecast' in turn(client, 'Weather in Hyderabad')['reply']


def test_validation_and_ui(client):
    for value in ['', '   ', 'x' * 501]:
        assert client.post('/api/chat', json={'message': value}).status_code == 422
    assert client.post('/api/chat', json={'message': 'hi', 'thread_id': 'invalid'}).status_code == 422
    assert client.get('/').status_code == 200
    assert client.get('/docs').status_code == 200


def test_help_and_unsupported_dates(client):
    assert 'help' in turn(client, 'hello')['nodes_visited']
    assert 'help' in turn(client, 'Weather in London next week')['nodes_visited']
