<?php

use App\Jobs\ProcessMpesaPayment;
use App\Models\MpesaTransaction;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Queue;
use Tests\Integration\Mpesa\MpesaTestCase;

uses(MpesaTestCase::class);

afterEach(function () {
    DB::table('mpesa_transactions')->truncate();
});

function validC2BPayload(string $transId): array
{
    return [
        'TransactionType' => 'Pay Bill',
        'TransID' => $transId,
        'TransTime' => now()->format('YmdHis'),
        'TransAmount' => '1500.00',
        'BusinessShortCode' => '174379',
        'BillRefNumber' => 'NRB-KHC-B04',
        'MSISDN' => '254712345678',
    ];
}

test('a valid callback stores the raw payload and dispatches the payments job', function () {
    Queue::fake();

    $response = $this->postJson('/api/v1/callbacks/mpesa/c2b', validC2BPayload('QBX000TRANS1'));

    $response->assertOk()->assertJson([
        'ResultCode' => 0,
        'ResultDesc' => 'Accepted',
    ]);

    $this->assertDatabaseHas('mpesa_transactions', ['trans_id' => 'QBX000TRANS1']);

    Queue::assertPushedOn('payments', ProcessMpesaPayment::class, function ($job) {
        return $job->transId === 'QBX000TRANS1';
    });
});

test('duplicate trans_id creates exactly one row and does not dispatch twice', function () {
    Queue::fake();

    $payload = validC2BPayload('QBX000TRANS2');

    $this->postJson('/api/v1/callbacks/mpesa/c2b', $payload)->assertOk();
    $this->postJson('/api/v1/callbacks/mpesa/c2b', $payload)->assertOk();

    expect(MpesaTransaction::where('trans_id', 'QBX000TRANS2')->count())->toBe(1);

    Queue::assertPushed(ProcessMpesaPayment::class, 1);
});

test('missing required fields are rejected', function () {
    $response = $this->postJson('/api/v1/callbacks/mpesa/c2b', ['TransID' => 'QBX000TRANS3']);

    $response->assertStatus(422);
});

test('ProcessMpesaPayment is idempotent when run twice for the same transaction', function () {
    $transaction = MpesaTransaction::create([
        'trans_id' => 'QBX000TRANS4',
        'raw_payload' => validC2BPayload('QBX000TRANS4'),
    ]);

    (new ProcessMpesaPayment('QBX000TRANS4'))->handle();

    $transaction->refresh();
    $firstProcessedAt = $transaction->processed_at;
    expect($firstProcessedAt)->not->toBeNull();

    (new ProcessMpesaPayment('QBX000TRANS4'))->handle();

    $transaction->refresh();
    expect($transaction->processed_at->equalTo($firstProcessedAt))->toBeTrue();
});
