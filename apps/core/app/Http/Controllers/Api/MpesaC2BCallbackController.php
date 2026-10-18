<?php

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Http\Requests\MpesaC2BCallbackRequest;
use App\Jobs\ProcessMpesaPayment;
use App\Models\MpesaTransaction;
use Illuminate\Database\QueryException;
use Illuminate\Http\JsonResponse;
use Illuminate\Support\Facades\DB;

class MpesaC2BCallbackController extends Controller
{
    public function __invoke(MpesaC2BCallbackRequest $request): JsonResponse
    {
        $transId = $request->validated('TransID');

        $isNewTransaction = true;

        try {
            DB::transaction(function () use ($request, $transId): void {
                MpesaTransaction::create([
                    'trans_id' => $transId,
                    'raw_payload' => $request->all(),
                ]);
            });
        } catch (QueryException $exception) {
            if (! $this->isUniqueConstraintViolation($exception)) {
                throw $exception;
            }

            // trans_id already stored (duplicate Daraja delivery): do not
            // create a second row or dispatch a duplicate job.
            $isNewTransaction = false;
        }

        if ($isNewTransaction) {
            DB::afterCommit(fn () => ProcessMpesaPayment::dispatch($transId));
        }

        return response()->json([
            'ResultCode' => 0,
            'ResultDesc' => 'Accepted',
        ]);
    }

    private function isUniqueConstraintViolation(QueryException $exception): bool
    {
        return in_array($exception->getCode(), ['23000', '23505'], true);
    }
}
