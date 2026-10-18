<?php

namespace Tests\Integration\Mpesa;

use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Foundation\Testing\RefreshDatabaseState;
use Tests\TestCase;

/**
 * The controller dispatches ProcessMpesaPayment via DB::afterCommit(), which
 * never fires if the test runs inside RefreshDatabase's default per-test
 * transaction wrapper (it rolls back instead of committing, so the callback
 * is discarded). This overrides beginDatabaseTransaction() to skip that
 * wrapping while still caching the in-memory SQLite PDO the same way the
 * parent trait does, so the migrated schema and connection are still reused
 * across tests. Tests truncate their own tables instead of relying on
 * rollback for isolation.
 */
abstract class MpesaTestCase extends TestCase
{
    use RefreshDatabase;

    public function beginDatabaseTransaction()
    {
        $database = $this->app->make('db');

        foreach ($this->connectionsToTransact() as $name) {
            $connection = $database->connection($name);

            if ($this->usingInMemoryDatabase($name)) {
                RefreshDatabaseState::$inMemoryConnections[$name] ??= $connection->getPdo();
            }
        }
    }
}
