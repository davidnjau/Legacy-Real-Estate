<?php

namespace App\Console\Commands;

use App\Jobs\ProcessMpesaPayment;
use App\Models\MpesaTransaction;
use Illuminate\Console\Command;

class SweepUnprocessedMpesaTransactions extends Command
{
    protected $signature = 'mpesa:sweep-unprocessed';

    protected $description = 'Re-queue stored M-Pesa callbacks left unprocessed for more than 5 minutes';

    public function handle(): void
    {
        $stale = MpesaTransaction::whereNull('processed_at')
            ->where('created_at', '<=', now()->subMinutes(5))
            ->pluck('trans_id');

        foreach ($stale as $transId) {
            ProcessMpesaPayment::dispatch($transId);
        }

        $this->info("Re-queued {$stale->count()} unprocessed M-Pesa transaction(s).");
    }
}
