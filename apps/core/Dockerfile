FROM php:8.3-cli AS vendor

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev libzip-dev \
    && docker-php-ext-install pdo_pgsql bcmath pcntl zip \
    && rm -rf /var/lib/apt/lists/*
COPY --from=composer:2 /usr/bin/composer /usr/bin/composer

WORKDIR /app
COPY composer.json composer.lock ./
RUN composer install --no-dev --no-scripts --no-interaction --prefer-dist --no-progress --optimize-autoloader

FROM php:8.3-cli AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev libzip-dev \
    && docker-php-ext-install pdo_pgsql bcmath pcntl zip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=vendor /app/vendor ./vendor
COPY . .
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
    && php artisan package:discover --ansi

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["web"]
