<?php

use App\Http\Controllers\Api\HealthController;
use App\Http\Controllers\Api\MpesaC2BCallbackController;
use Illuminate\Support\Facades\Route;

Route::prefix('v1')->group(function () {
    Route::get('/health', HealthController::class);

    Route::prefix('callbacks/mpesa')->group(function () {
        Route::post('/c2b', MpesaC2BCallbackController::class);
    });
});
