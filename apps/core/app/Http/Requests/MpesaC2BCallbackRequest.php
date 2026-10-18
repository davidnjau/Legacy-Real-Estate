<?php

namespace App\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;

/**
 * Validates the subset of the Safaricom Daraja C2B confirmation payload
 * this phase actually persists and acts on.
 */
class MpesaC2BCallbackRequest extends FormRequest
{
    public function authorize(): bool
    {
        return true;
    }

    public function rules(): array
    {
        return [
            'TransactionType' => ['required', 'string'],
            'TransID' => ['required', 'string'],
            'TransTime' => ['required', 'string'],
            'TransAmount' => ['required', 'numeric'],
            'BusinessShortCode' => ['required', 'string'],
            'BillRefNumber' => ['required', 'string'],
            'MSISDN' => ['required', 'string'],
        ];
    }
}
