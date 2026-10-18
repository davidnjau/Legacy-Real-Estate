<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;

class MpesaTransaction extends Model
{
    use HasFactory;

    protected $fillable = [
        'trans_id',
        'raw_payload',
        'processed_at',
    ];

    protected function casts(): array
    {
        return [
            'raw_payload' => 'array',
            'processed_at' => 'datetime',
        ];
    }
}
