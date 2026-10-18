<?php

namespace App\Jobs;

use App\Models\MpesaTransaction;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Bus\Dispatchable;
use Illuminate\Queue\InteractsWithQueue;
use Illuminate\Queue\SerializesModels;

class ProcessMpesaPayment implements ShouldQueue
{
    use Dispatchable, InteractsWithQueue, SerializesModels;

    public string $queue = 'payments';

    public int $tries = 5;

    public function __construct(public string $transId) {}

    /**
     * Mirrors the payments supervisor's backoff in config/horizon.php, so
     * retries behave the same even when run outside Horizon (e.g. plain
     * `queue:work` or tests).
     */
    public function backoff(): array
    {
        return [10, 30, 60, 120, 300];
    }

    /**
     * Idempotent on trans_id: a transaction already marked processed_at
     * is a no-op, so retries/duplicate dispatches (e.g. from the sweeper)
     * never double-process the same payment.
     */
    public function handle(): void
    {
        $transaction = MpesaTransaction::where('trans_id', $this->transId)->first();

        if (! $transaction || $transaction->processed_at !== null) {
            return;
        }

        // Stub: real payment reconciliation (matching to tenancy/invoice,
        // ledger entries, notifications) lands in a later phase.
        $transaction->update(['processed_at' => now()]);
    }
}
