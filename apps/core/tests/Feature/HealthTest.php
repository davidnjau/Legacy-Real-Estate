<?php

use Spectator\Spectator;

test('health endpoint returns ok', function () {
    $this->getJson('/api/v1/health')
        ->assertOk()
        ->assertJson(['status' => 'ok']);
});

test('health endpoint matches the openapi contract stub', function () {
    Spectator::using('openapi.yaml');

    $this->getJson('/api/v1/health')
        ->assertValidRequest()
        ->assertValidResponse(200);
});
