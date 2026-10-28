-- Application profiles mirror Supabase Auth IDs; credentials remain in auth.users.
create table if not exists public.users (
  id uuid primary key references auth.users(id) on delete cascade,
  full_name text,
  email text unique,
  phone text,
  role text not null check (role in ('landlord', 'caretaker', 'tenant')),
  status text not null default 'active' check (status in ('active', 'dormant')),
  password_hash text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.users enable row level security;
grant select on public.users to authenticated;

do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'users' and policyname = 'users_select_own_profile'
  ) then
    create policy users_select_own_profile on public.users
      for select to authenticated using (auth.uid() = id);
  end if;
end $$;

-- Required by the landlord staff wizard and /caretaker-signup flow.
alter table public.users add column if not exists onboarding_completed boolean not null default false;
alter table public.users add column if not exists tutorial_completed boolean not null default false;
alter table public.users add column if not exists property_name text;

-- Property ownership foundation for the multi-property PangaKodi workspace.
create table if not exists public.properties (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references public.users(id) on delete cascade,
  name text not null,
  address text,
  timezone text not null default 'Africa/Nairobi',
  currency_code text not null default 'KES',
  default_rent numeric(12, 2) not null default 0 check (default_rent >= 0),
  security_deposit numeric(12, 2) not null default 0 check (security_deposit >= 0),
  water_deposit numeric(12, 2) not null default 0 check (water_deposit >= 0),
  default_garbage_charge numeric(12, 2) not null default 0 check (default_garbage_charge >= 0),
  default_water_rate numeric(12, 2) not null default 0 check (default_water_rate >= 0),
  rent_due_day smallint not null default 10 check (rent_due_day between 1 and 28),
  billing_mode text not null default 'metered_water' check (billing_mode in ('metered_water', 'fixed_utilities')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (owner_id, name)
);

alter table public.properties add column if not exists address text;
alter table public.properties add column if not exists timezone text not null default 'Africa/Nairobi';
alter table public.properties add column if not exists currency_code text not null default 'KES';
alter table public.properties add column if not exists default_rent numeric(12, 2) not null default 0;
alter table public.properties add column if not exists security_deposit numeric(12, 2) not null default 0;
alter table public.properties add column if not exists water_deposit numeric(12, 2) not null default 0;
alter table public.properties add column if not exists default_garbage_charge numeric(12, 2) not null default 0;
alter table public.properties add column if not exists default_water_rate numeric(12, 2) not null default 0;
alter table public.properties add column if not exists rent_due_day smallint not null default 10;
alter table public.properties add column if not exists billing_mode text not null default 'metered_water';

create table if not exists public.property_members (
  property_id uuid not null references public.properties(id) on delete cascade,
  user_id uuid not null references public.users(id) on delete cascade,
  member_role text not null check (member_role in ('owner', 'manager', 'caretaker')),
  created_at timestamptz not null default now(),
  primary key (property_id, user_id)
);

create table if not exists public.property_recurring_charges (
  id uuid primary key default gen_random_uuid(),
  property_id uuid not null references public.properties(id) on delete cascade,
  name text not null,
  amount numeric(12, 2) not null check (amount >= 0),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (property_id, name)
);

create table if not exists public.property_announcements (
  id uuid primary key default gen_random_uuid(),
  property_id uuid not null references public.properties(id) on delete cascade,
  created_by uuid not null references public.users(id) on delete restrict,
  title text not null check (length(trim(title)) between 1 and 120),
  message text not null check (length(trim(message)) between 1 and 4000),
  audience text not null check (audience in ('property', 'units')),
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);

create table if not exists public.property_announcement_units (
  announcement_id uuid not null references public.property_announcements(id) on delete cascade,
  unit_id uuid not null references public.units(id) on delete cascade,
  primary key (announcement_id, unit_id)
);

alter table public.invoices
  add column if not exists recurring_charges jsonb not null default '[]'::jsonb;
alter table public.invoices
  add column if not exists water_reading_missing boolean not null default false;

create index if not exists property_announcements_active_idx
  on public.property_announcements(property_id, expires_at, created_at desc);
create index if not exists property_announcement_units_unit_idx
  on public.property_announcement_units(unit_id, announcement_id);
alter table public.property_announcements enable row level security;
alter table public.property_announcement_units enable row level security;

alter table public.users add column if not exists active_property_id uuid references public.properties(id) on delete set null;
alter table public.units add column if not exists property_id uuid references public.properties(id) on delete restrict;

insert into public.properties(owner_id, name)
select id, coalesce(nullif(trim(property_name), ''), 'My Property')
from public.users
where role = 'landlord'
on conflict (owner_id, name) do nothing;

insert into public.property_members(property_id, user_id, member_role)
select property.id, property.owner_id, 'owner'
from public.properties property
on conflict (property_id, user_id) do nothing;

update public.users profile
set active_property_id = (
  select property.id
  from public.properties property
  where property.owner_id = profile.id
  order by property.created_at, property.id
  limit 1
)
where profile.role = 'landlord'
  and profile.active_property_id is null;

-- Old installations had one implicit property. Do not guess ownership if there
-- are multiple landlords; those unassigned units require an explicit mapping.
do $$
begin
  if (select count(*) from public.users where role = 'landlord') = 1 then
    update public.units unit
    set property_id = profile.active_property_id
    from public.users profile
    where profile.role = 'landlord'
      and unit.property_id is null;
  end if;
end $$;

create index if not exists properties_owner_idx on public.properties(owner_id);
create index if not exists property_members_user_idx on public.property_members(user_id, property_id);
create index if not exists units_property_idx on public.units(property_id, unit_code);
create index if not exists property_recurring_charges_property_idx on public.property_recurring_charges(property_id, is_active);

alter table public.invoices add column if not exists property_id uuid references public.properties(id) on delete restrict;
update public.invoices invoice
set property_id = unit.property_id
from public.units unit
where unit.id = invoice.unit_id
  and invoice.property_id is null
  and unit.property_id is not null;
create index if not exists invoices_property_month_idx on public.invoices(property_id, billing_month);

alter table public.properties enable row level security;
alter table public.property_members enable row level security;
alter table public.property_recurring_charges enable row level security;

grant select on public.properties, public.property_members, public.property_recurring_charges to authenticated;

do $$
begin
  if not exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'properties' and policyname = 'property_members_can_read') then
    create policy property_members_can_read on public.properties for select to authenticated
      using (exists (select 1 from public.property_members membership where membership.property_id = id and membership.user_id = auth.uid()));
  end if;
  if not exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'property_members' and policyname = 'members_can_read_self') then
    create policy members_can_read_self on public.property_members for select to authenticated
      using (user_id = auth.uid());
  end if;
  if not exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'property_recurring_charges' and policyname = 'property_members_can_read_charges') then
    create policy property_members_can_read_charges on public.property_recurring_charges for select to authenticated
      using (exists (select 1 from public.property_members membership where membership.property_id = public.property_recurring_charges.property_id and membership.user_id = auth.uid()));
  end if;
end $$;

create table if not exists public.caretaker_invites (
  id uuid primary key default gen_random_uuid(),
  code text not null unique,
  created_by uuid not null references public.users(id) on delete cascade,
  expires_at timestamptz not null,
  used_by uuid references public.users(id) on delete set null,
  used_at timestamptz,
  status text not null default 'active' check (status in ('active', 'used', 'expired')),
  created_at timestamptz not null default now()
);

alter table public.caretaker_invites add column if not exists property_id uuid references public.properties(id) on delete cascade;
alter table public.caretaker_invites add column if not exists email text;
alter table public.caretaker_invites add column if not exists auth_user_id uuid references auth.users(id) on delete set null;

update public.caretaker_invites invite
set property_id = profile.active_property_id
from public.users profile
where profile.id = invite.created_by
  and invite.property_id is null
  and profile.active_property_id is not null;

insert into public.property_members(property_id, user_id, member_role)
select invite.property_id, invite.used_by, 'caretaker'
from public.caretaker_invites invite
where invite.property_id is not null
  and invite.used_by is not null
on conflict (property_id, user_id) do nothing;

create index if not exists caretaker_invites_created_by_idx
  on public.caretaker_invites(created_by);

create index if not exists caretaker_invites_active_code_idx
  on public.caretaker_invites(code, status);
create index if not exists caretaker_invites_email_property_idx
  on public.caretaker_invites(property_id, lower(email), created_at desc)
  where email is not null;
create unique index if not exists caretaker_invites_one_active_email_per_property_idx
  on public.caretaker_invites(property_id, lower(email))
  where status = 'active' and email is not null;

alter table public.caretaker_invites enable row level security;

create table if not exists public.property_staff (
  id uuid primary key default gen_random_uuid(),
  property_id uuid not null references public.properties(id) on delete cascade,
  full_name text not null,
  email text,
  phone text,
  staff_role text not null,
  notes text,
  created_by uuid not null references public.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists property_staff_property_id_idx on public.property_staff(property_id);
alter table public.property_staff enable row level security;

-- Columns required by the migration wizard and arrears editor.
do $$
begin
  alter type public.user_status add value if not exists 'dormant';
exception
  when undefined_object then null;
end $$;

do $$
begin
  alter type public.unit_status add value if not exists 'booked';
exception
  when undefined_object then null;
end $$;

alter table public.units
  add column if not exists floor_number text;

alter table public.units
  add column if not exists is_migrated boolean not null default false;

-- Existing migrated tenants use Ksh 8,000 as the default deposit when no
-- deposit was recorded during the initial migration.
update public.units
set deposit_amount = 8000
where is_migrated = true
  and status = 'occupied'
  and coalesce(deposit_amount, 0) = 0;

alter table public.unit_assignments
  add column if not exists opening_arrears numeric(12, 2) not null default 0;

alter table public.unit_assignments
  add column if not exists migration_arrears_locked boolean not null default false;

alter table public.unit_assignments
  add column if not exists migration_arrears_consumed_at timestamptz;

do $$
begin
  if not exists (
    select 1
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'unit_assignments'
      and column_name = 'is_migrated'
  ) then
    alter table public.unit_assignments
      add column is_migrated boolean not null default false;

    update public.unit_assignments assignment
    set is_migrated = true
    from public.units unit
    where unit.id = assignment.unit_id
      and unit.is_migrated = true;
  end if;
end $$;

alter table public.unit_assignments
  add column if not exists updated_at timestamptz not null default now();

create table if not exists public.migration_arrears_payments (
  id uuid primary key default gen_random_uuid(),
  assignment_id uuid not null references public.unit_assignments(id) on delete cascade,
  amount numeric(12, 2) not null check (amount > 0),
  mpesa_reference text,
  payment_date date not null,
  recorded_by uuid not null references public.users(id),
  created_at timestamptz not null default now()
);

create index if not exists migration_arrears_payments_assignment_idx
  on public.migration_arrears_payments(assignment_id, payment_date);

-- Required by the maintenance conversation API.
create table if not exists public.maintenance_comments (
  id uuid primary key default gen_random_uuid(),
  ticket_id uuid not null references public.maintenance_tickets(id) on delete cascade,
  sender_id uuid not null references public.users(id) on delete cascade,
  sender_role text not null check (sender_role in ('landlord', 'caretaker', 'tenant')),
  sender_name text not null,
  message text not null,
  created_at timestamptz not null default now()
);

create index if not exists maintenance_comments_ticket_idx
  on public.maintenance_comments(ticket_id, created_at);

alter table public.maintenance_comments enable row level security;

-- Move-in charges and payment history for new tenants.
create table if not exists public.move_in_settlements (
  id uuid primary key default gen_random_uuid(),
  unit_id uuid not null references public.units(id) on delete cascade,
  tenant_id uuid references public.users(id) on delete set null,
  tenant_name text not null,
  tenant_email text,
  tenant_phone text not null,
  deposit_amount numeric(12, 2) not null default 0,
  rent_amount numeric(12, 2) not null default 0,
  garbage_amount numeric(12, 2) not null default 0,
  water_deposit_amount numeric(12, 2) not null default 0,
  total_required numeric(12, 2) not null default 0,
  total_paid numeric(12, 2) not null default 0,
  deposit_paid_amount numeric(12, 2) not null default 0,
  outstanding_balance numeric(12, 2) not null default 0,
  expected_move_in_date date,
  status text not null default 'payment_pending' check (status in ('payment_pending', 'partial', 'booked', 'paid', 'move_in_pending', 'active', 'reserved', 'cancelled')),
  activation_override boolean not null default false,
  cancellation_reason text,
  cancelled_at timestamptz,
  booked_at timestamptz,
  activated_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.move_in_payments (
  id uuid primary key default gen_random_uuid(),
  settlement_id uuid not null references public.move_in_settlements(id) on delete cascade,
  amount numeric(12, 2) not null check (amount > 0),
  method text not null check (method in ('manual', 'stk_push')),
  mpesa_reference text,
  checkout_request_id text,
  recorded_by uuid not null references public.users(id),
  created_at timestamptz not null default now()
);

alter table public.move_in_payments add column if not exists processed_at timestamptz;
alter table public.move_in_payments add column if not exists result_description text;
alter table public.move_in_payments add column if not exists status text not null default 'success';
alter table public.move_in_payments add column if not exists checkout_request_id text;
alter table public.move_in_payments drop constraint if exists move_in_payments_status_check;
alter table public.move_in_payments add constraint move_in_payments_status_check
  check (status in ('pending', 'success', 'failed', 'needs_review'));
create unique index if not exists move_in_payments_checkout_unique
  on public.move_in_payments(checkout_request_id)
  where checkout_request_id is not null;

create table if not exists public.invoice_payments (
  id uuid primary key default gen_random_uuid(),
  invoice_id uuid not null references public.invoices(id) on delete restrict,
  amount numeric(12, 2) not null check (amount > 0),
  method text not null check (method in ('cash', 'bank_transfer', 'mpesa_manual', 'stk_push')),
  reference text,
  checkout_request_id text,
  recorded_by uuid not null references public.users(id),
  paid_at timestamptz not null default now()
);

alter table public.invoice_payments add column if not exists checkout_request_id text;

create index if not exists invoice_payments_invoice_paid_idx
  on public.invoice_payments(invoice_id, paid_at desc);
create unique index if not exists invoice_payments_checkout_unique
  on public.invoice_payments(checkout_request_id)
  where checkout_request_id is not null;
alter table public.invoice_payments enable row level security;

create index if not exists move_in_settlements_unit_idx on public.move_in_settlements(unit_id, status);
create index if not exists move_in_payments_settlement_idx on public.move_in_payments(settlement_id, created_at);

alter table public.move_in_settlements
  add column if not exists deposit_paid_amount numeric(12, 2) not null default 0;

update public.move_in_settlements
set deposit_paid_amount = least(
  coalesce(total_paid, 0),
  coalesce(deposit_amount, 0) + coalesce(water_deposit_amount, 0)
)
where deposit_paid_amount = 0
  and total_paid > 0;

create or replace function public.record_move_in_payment(
  p_settlement_id uuid,
  p_amount numeric,
  p_method text,
  p_reference text,
  p_recorded_by uuid,
  p_checkout_request_id text default null,
  p_is_callback boolean default false
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_settlement public.move_in_settlements%rowtype;
  v_payment public.move_in_payments%rowtype;
  v_new_paid numeric(12, 2);
  v_new_deposit_paid numeric(12, 2);
  v_new_balance numeric(12, 2);
  v_new_status text;
begin
  if p_amount is null or p_amount <= 0 or p_method not in ('manual', 'stk_push') then
    return jsonb_build_object('applied', false, 'reason', 'invalid_payment');
  end if;

  if p_is_callback then
    select * into v_payment
    from public.move_in_payments
    where checkout_request_id = p_checkout_request_id
    for update;
    if not found then
      return jsonb_build_object('applied', false, 'reason', 'attempt_not_found');
    end if;
    if v_payment.status = 'success' then
      return jsonb_build_object('applied', false, 'duplicate', true);
    end if;
    if v_payment.status <> 'pending' or v_payment.settlement_id <> p_settlement_id or v_payment.amount <> p_amount then
      update public.move_in_payments
      set status = 'needs_review', result_description = 'Callback did not match the pending settlement payment.'
      where id = v_payment.id;
      return jsonb_build_object('applied', false, 'reason', 'callback_mismatch');
    end if;
  end if;

  select * into v_settlement
  from public.move_in_settlements
  where id = p_settlement_id
  for update;
  if not found or v_settlement.status = 'cancelled' then
    return jsonb_build_object('applied', false, 'reason', 'settlement_not_found');
  end if;
  if p_amount > coalesce(v_settlement.outstanding_balance, 0) then
    if p_is_callback then
      update public.move_in_payments
      set status = 'needs_review', result_description = 'Settlement balance changed before callback processing.'
      where id = v_payment.id;
    end if;
    return jsonb_build_object('applied', false, 'reason', 'balance_changed');
  end if;

  v_new_paid := coalesce(v_settlement.total_paid, 0) + p_amount;
  v_new_deposit_paid := coalesce(v_settlement.deposit_paid_amount, 0)
    + least(
      p_amount,
      greatest(
        0,
        coalesce(v_settlement.deposit_amount, 0)
          + coalesce(v_settlement.water_deposit_amount, 0)
          - coalesce(v_settlement.deposit_paid_amount, 0)
      )
    );
  v_new_balance := greatest(0, coalesce(v_settlement.total_required, 0) - v_new_paid);
  v_new_status := case
    when v_settlement.activated_at is not null then 'active'
    when v_new_balance = 0 then 'paid'
    when v_settlement.activation_override then 'move_in_pending'
    when v_new_deposit_paid >= coalesce(v_settlement.deposit_amount, 0) + coalesce(v_settlement.water_deposit_amount, 0) then 'booked'
    else 'partial'
  end;

  update public.move_in_settlements
  set total_paid = v_new_paid,
      deposit_paid_amount = v_new_deposit_paid,
      outstanding_balance = v_new_balance,
      status = v_new_status,
      booked_at = case when v_new_deposit_paid >= coalesce(deposit_amount, 0) + coalesce(water_deposit_amount, 0) then coalesce(booked_at, now()) else booked_at end,
      updated_at = now()
  where id = p_settlement_id;

  if v_new_deposit_paid >= coalesce(v_settlement.deposit_amount, 0) + coalesce(v_settlement.water_deposit_amount, 0) then
    update public.units
    set status = 'booked', updated_at = now()
    where id = v_settlement.unit_id and status = 'vacant';
  end if;

  if p_is_callback then
    update public.move_in_payments
    set amount = p_amount,
        mpesa_reference = p_reference,
        status = 'success',
        result_description = null,
        processed_at = now()
    where id = v_payment.id;
  else
    insert into public.move_in_payments(settlement_id, amount, method, mpesa_reference, recorded_by, status, processed_at)
    values (p_settlement_id, p_amount, p_method, nullif(p_reference, ''), p_recorded_by, 'success', now());
  end if;

  return jsonb_build_object('applied', true, 'total_paid', v_new_paid, 'deposit_paid_amount', v_new_deposit_paid, 'outstanding_balance', v_new_balance, 'status', v_new_status);
end;
$$;

revoke all on function public.record_move_in_payment(uuid, numeric, text, text, uuid, text, boolean) from public, anon, authenticated;
grant execute on function public.record_move_in_payment(uuid, numeric, text, text, uuid, text, boolean) to service_role;

create or replace function public.record_invoice_payment(
  p_invoice_id uuid,
  p_amount numeric,
  p_method text,
  p_reference text,
  p_recorded_by uuid
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_invoice public.invoices%rowtype;
  v_paid numeric(12, 2);
  v_balance numeric(12, 2);
  v_status text;
begin
  if p_amount is null or p_amount <= 0 or p_method not in ('cash', 'bank_transfer', 'mpesa_manual') then
    return jsonb_build_object('applied', false, 'reason', 'invalid_payment');
  end if;
  select * into v_invoice from public.invoices where id = p_invoice_id for update;
  if not found then return jsonb_build_object('applied', false, 'reason', 'invoice_not_found'); end if;
  if p_amount > coalesce(v_invoice.outstanding_balance, 0) then
    return jsonb_build_object('applied', false, 'reason', 'amount_exceeds_balance');
  end if;

  v_paid := coalesce(v_invoice.amount_paid, 0) + p_amount;
  v_balance := greatest(0, coalesce(v_invoice.outstanding_balance, 0) - p_amount);
  v_status := case
    when v_invoice.status = 'draft' then 'draft'
    when v_balance = 0 then 'paid'
    when v_paid > 0 then 'partial'
    else v_invoice.status
  end;

  update public.invoices
  set amount_paid = v_paid, outstanding_balance = v_balance, status = v_status, updated_at = now()
  where id = p_invoice_id;
  insert into public.invoice_payments(invoice_id, amount, method, reference, recorded_by)
  values (p_invoice_id, p_amount, p_method, nullif(p_reference, ''), p_recorded_by);

  return jsonb_build_object('applied', true, 'amount_paid', v_paid, 'outstanding_balance', v_balance, 'status', v_status);
end;
$$;

revoke all on function public.record_invoice_payment(uuid, numeric, text, text, uuid) from public, anon, authenticated;
grant execute on function public.record_invoice_payment(uuid, numeric, text, text, uuid) to service_role;

create table if not exists public.billing_settings (
  id boolean primary key default true check (id = true),
  manual_payment_deadline date,
  paybill_number text,
  account_number text,
  first_online_billing_month text,
  updated_by uuid references public.users(id),
  updated_at timestamptz not null default now()
);

create table if not exists public.property_billing_settings (
  property_id uuid primary key references public.properties(id) on delete cascade,
  paybill_number text,
  account_number text,
  universal_security_deposit numeric(12, 2) not null default 8000,
  universal_water_deposit numeric(12, 2) not null default 500,
  first_online_billing_month text,
  updated_by uuid references public.users(id),
  updated_at timestamptz not null default now()
);

insert into public.property_billing_settings(
  property_id, paybill_number, account_number, universal_security_deposit,
  universal_water_deposit, first_online_billing_month, updated_by, updated_at
)
select property.id, legacy.paybill_number, legacy.account_number,
       coalesce(legacy.universal_security_deposit, 8000),
       coalesce(legacy.universal_water_deposit, 500),
       legacy.first_online_billing_month, legacy.updated_by, coalesce(legacy.updated_at, now())
from public.properties property
left join public.billing_settings legacy on legacy.id = true
on conflict (property_id) do nothing;

alter table public.property_billing_settings enable row level security;
grant select on public.property_billing_settings to authenticated;
do $$
begin
  if not exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'property_billing_settings' and policyname = 'property_members_read_billing_settings') then
    create policy property_members_read_billing_settings on public.property_billing_settings for select to authenticated
      using (exists (select 1 from public.property_members membership where membership.property_id = property_billing_settings.property_id and membership.user_id = auth.uid()));
  end if;
end $$;

-- Safe upgrades for projects that already created the settlement tables.
alter table public.move_in_settlements alter column tenant_email drop not null;
alter table public.move_in_settlements add column if not exists activation_override boolean not null default false;
alter table public.move_in_settlements add column if not exists cancellation_reason text;
alter table public.move_in_settlements add column if not exists cancelled_at timestamptz;
alter table public.move_in_settlements add column if not exists booked_at timestamptz;
alter table public.move_in_settlements add column if not exists activated_at timestamptz;
alter table public.move_in_settlements add column if not exists claim_code_issued_at timestamptz;
alter table public.move_in_settlements add column if not exists utility_charges jsonb not null default '[]'::jsonb;
alter table public.move_in_settlements add column if not exists activation_email_sent_at timestamptz;
alter table public.move_in_settlements add column if not exists activation_email_error text;

create or replace function public.create_move_in_reservation(
  p_unit_id uuid,
  p_property_id uuid,
  p_tenant_name text,
  p_tenant_email text,
  p_tenant_phone text,
  p_deposit_amount numeric,
  p_rent_amount numeric,
  p_garbage_amount numeric,
  p_water_deposit_amount numeric,
  p_utility_charges jsonb,
  p_expected_move_in_date date
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_unit public.units%rowtype;
  v_settlement public.move_in_settlements%rowtype;
  v_utility_total numeric(12, 2);
begin
  select * into v_unit
  from public.units
  where id = p_unit_id and property_id = p_property_id
  for update;

  if not found then
    return jsonb_build_object('success', false, 'reason', 'unit_not_found');
  end if;
  if v_unit.status <> 'vacant' then
    return jsonb_build_object('success', false, 'reason', 'unit_not_vacant');
  end if;
  if exists (
    select 1 from public.move_in_settlements
    where unit_id = p_unit_id
      and status in ('payment_pending', 'partial', 'booked', 'paid', 'move_in_pending', 'reserved')
  ) then
    return jsonb_build_object('success', false, 'reason', 'unit_has_open_reservation');
  end if;
  if exists (
    select 1 from public.move_in_settlements
    where lower(tenant_email) = lower(p_tenant_email)
      and status in ('payment_pending', 'partial', 'booked', 'paid', 'move_in_pending', 'reserved')
  ) then
    return jsonb_build_object('success', false, 'reason', 'email_has_open_reservation');
  end if;

  select coalesce(sum((charge->>'amount')::numeric), 0)
  into v_utility_total
  from jsonb_array_elements(coalesce(p_utility_charges, '[]'::jsonb)) as charge;

  insert into public.move_in_settlements (
    unit_id, tenant_name, tenant_email, tenant_phone, deposit_amount,
    rent_amount, garbage_amount, water_deposit_amount, utility_charges,
    total_required, total_paid, deposit_paid_amount, outstanding_balance,
    expected_move_in_date, status
  ) values (
    p_unit_id, p_tenant_name, lower(p_tenant_email), p_tenant_phone, p_deposit_amount,
    p_rent_amount, p_garbage_amount, p_water_deposit_amount, coalesce(p_utility_charges, '[]'::jsonb),
    p_deposit_amount + p_rent_amount + p_garbage_amount + p_water_deposit_amount + v_utility_total,
    0, 0,
    p_deposit_amount + p_rent_amount + p_garbage_amount + p_water_deposit_amount + v_utility_total,
    p_expected_move_in_date, 'payment_pending'
  )
  returning * into v_settlement;

  return jsonb_build_object('success', true, 'settlement', to_jsonb(v_settlement));
end;
$$;

revoke all on function public.create_move_in_reservation(uuid, uuid, text, text, text, numeric, numeric, numeric, numeric, jsonb, date) from public, anon, authenticated;
grant execute on function public.create_move_in_reservation(uuid, uuid, text, text, text, numeric, numeric, numeric, numeric, jsonb, date) to service_role;

update public.move_in_settlements
set claim_code_issued_at = activated_at
where claim_code_issued_at is null
  and activated_at is not null;
alter table public.move_in_settlements drop constraint if exists move_in_settlements_status_check;
alter table public.move_in_settlements add constraint move_in_settlements_status_check
  check (status in ('payment_pending', 'partial', 'booked', 'paid', 'move_in_pending', 'active', 'reserved', 'cancelled'));

-- Distinguish move-in baselines from billable monthly meter readings.
alter table public.water_readings
  add column if not exists reading_type text not null default 'monthly';

alter table public.water_readings
  drop constraint if exists water_readings_reading_type_check;

alter table public.water_readings
  add constraint water_readings_reading_type_check
  check (reading_type in ('baseline', 'monthly'));

-- Remove either version of the uniqueness rule before changing reading types or
-- deduplicating legacy rows. This makes the migration safe to rerun.
alter table public.water_readings
  drop constraint if exists water_readings_unit_date_type_unique;

drop index if exists public.water_readings_unit_date_type_unique;
drop index if exists public.water_readings_unit_date_unique;

-- Migration readings are the starting baseline for existing tenants.
update public.water_readings reading
set reading_type = 'baseline'
where reading.reading_type = 'monthly'
  and exists (
    select 1
    from public.units unit
    where unit.id = reading.unit_id
      and unit.is_migrated = true
  )
  and not exists (
    select 1
    from public.water_readings later_reading
    where later_reading.unit_id = reading.unit_id
      and later_reading.reading_date < reading.reading_date
  );

delete from public.water_readings duplicate
where duplicate.ctid in (
  select duplicate_ctid
  from (
    select
      ctid as duplicate_ctid,
      row_number() over (
        partition by unit_id, reading_date, reading_type
        order by created_at desc nulls last, id desc
      ) as row_number
    from public.water_readings
  ) ranked
  where ranked.row_number > 1
);

create unique index if not exists water_readings_unit_date_type_unique
  on public.water_readings(unit_id, reading_date, reading_type);

-- Link recurring-rent STK callbacks to the requested invoice and process them once.
create table if not exists public.mpesa_invoice_attempts (
  checkout_request_id text primary key,
  merchant_request_id text,
  invoice_id uuid not null references public.invoices(id) on delete restrict,
  tenant_id uuid not null references public.users(id) on delete restrict,
  requested_amount numeric(12, 2) not null check (requested_amount > 0),
  paid_amount numeric(12, 2),
  phone_number text not null,
  mpesa_receipt text unique,
  transaction_date text,
  status text not null default 'pending' check (status in ('pending', 'success', 'failed')),
  result_description text,
  created_at timestamptz not null default now(),
  processed_at timestamptz
);

create index if not exists mpesa_invoice_attempts_invoice_idx
  on public.mpesa_invoice_attempts(invoice_id, created_at desc);

alter table public.mpesa_invoice_attempts enable row level security;

create or replace function public.apply_mpesa_invoice_payment(
  p_checkout_request_id text,
  p_mpesa_receipt text,
  p_amount numeric,
  p_phone_number text,
  p_transaction_date text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_attempt public.mpesa_invoice_attempts%rowtype;
  v_invoice public.invoices%rowtype;
  v_new_paid numeric(12, 2);
  v_new_balance numeric(12, 2);
  v_new_status text;
begin
  select * into v_attempt
  from public.mpesa_invoice_attempts
  where checkout_request_id = p_checkout_request_id
  for update;

  if not found then
    return jsonb_build_object('applied', false, 'reason', 'attempt_not_found');
  end if;

  if v_attempt.status = 'success' then
    return jsonb_build_object('applied', false, 'duplicate', true, 'invoice_id', v_attempt.invoice_id);
  end if;

  if v_attempt.status <> 'pending' then
    return jsonb_build_object('applied', false, 'reason', 'attempt_not_pending');
  end if;

  if p_amount is null or p_amount <= 0 or p_amount <> v_attempt.requested_amount then
    update public.mpesa_invoice_attempts
    set status = 'failed', result_description = 'Callback amount did not match the requested amount.', processed_at = now()
    where checkout_request_id = p_checkout_request_id;
    return jsonb_build_object('applied', false, 'reason', 'amount_mismatch');
  end if;

  select * into v_invoice
  from public.invoices
  where id = v_attempt.invoice_id
  for update;

  if not found then
    update public.mpesa_invoice_attempts
    set status = 'failed', result_description = 'Invoice no longer exists.', processed_at = now()
    where checkout_request_id = p_checkout_request_id;
    return jsonb_build_object('applied', false, 'reason', 'invoice_not_found');
  end if;

  v_new_paid := coalesce(v_invoice.amount_paid, 0) + p_amount;
  v_new_balance := greatest(0, coalesce(v_invoice.outstanding_balance, 0) - p_amount);
  v_new_status := case
    when v_invoice.status = 'draft' then 'draft'
    when v_new_balance = 0 then 'paid'
    when v_new_paid > 0 then 'partial'
    else v_invoice.status
  end;

  update public.invoices
  set amount_paid = v_new_paid,
      outstanding_balance = v_new_balance,
      status = v_new_status,
      updated_at = now()
  where id = v_invoice.id;

  update public.mpesa_invoice_attempts
  set status = 'success',
      paid_amount = p_amount,
      mpesa_receipt = p_mpesa_receipt,
      phone_number = coalesce(p_phone_number, phone_number),
      transaction_date = p_transaction_date,
      processed_at = now()
  where checkout_request_id = p_checkout_request_id;

  insert into public.invoice_payments(invoice_id, amount, method, reference, checkout_request_id, recorded_by, paid_at)
  values (v_invoice.id, p_amount, 'stk_push', p_mpesa_receipt, p_checkout_request_id, v_attempt.tenant_id, now());

  return jsonb_build_object(
    'applied', true,
    'invoice_id', v_invoice.id,
    'amount_paid', v_new_paid,
    'outstanding_balance', v_new_balance
  );
end;
$$;

revoke all on function public.apply_mpesa_invoice_payment(text, text, numeric, text, text) from public, anon, authenticated;
grant execute on function public.apply_mpesa_invoice_payment(text, text, numeric, text, text) to service_role;

-- Transition arrears accept both landlord-recorded and tenant STK payments.
alter table public.migration_arrears_payments
  add column if not exists method text not null default 'manual';
alter table public.migration_arrears_payments
  alter column recorded_by drop not null;
alter table public.migration_arrears_payments
  add column if not exists checkout_request_id text;
alter table public.migration_arrears_payments
  drop constraint if exists migration_arrears_payments_method_check;
alter table public.migration_arrears_payments
  add constraint migration_arrears_payments_method_check check (method in ('manual', 'stk_push'));
create unique index if not exists migration_arrears_payments_checkout_unique
  on public.migration_arrears_payments(checkout_request_id)
  where checkout_request_id is not null;

create table if not exists public.migration_arrears_payment_attempts (
  checkout_request_id text primary key,
  merchant_request_id text,
  assignment_id uuid not null references public.unit_assignments(id) on delete restrict,
  tenant_id uuid not null references public.users(id) on delete restrict,
  requested_amount numeric(12, 2) not null check (requested_amount > 0),
  phone_number text not null,
  status text not null default 'pending' check (status in ('pending', 'success', 'failed', 'needs_review')),
  result_description text,
  created_at timestamptz not null default now(),
  processed_at timestamptz
);

create index if not exists migration_arrears_attempt_assignment_idx
  on public.migration_arrears_payment_attempts(assignment_id, created_at desc);
alter table public.migration_arrears_payment_attempts enable row level security;

create or replace function public.record_migration_arrears_payment(
  p_assignment_id uuid,
  p_amount numeric,
  p_reference text,
  p_payment_date date,
  p_recorded_by uuid,
  p_method text,
  p_checkout_request_id text default null,
  p_mpesa_receipt text default null,
  p_phone_number text default null,
  p_transaction_date text default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_assignment public.unit_assignments%rowtype;
  v_attempt public.migration_arrears_payment_attempts%rowtype;
  v_invoice public.invoices%rowtype;
  v_first_month text;
  v_remaining numeric(12, 2);
  v_paid numeric(12, 2);
  v_balance numeric(12, 2);
  v_status text;
  v_has_published_invoice boolean := false;
begin
  if p_amount is null or p_amount <= 0 or p_method not in ('manual', 'stk_push') then
    return jsonb_build_object('applied', false, 'reason', 'invalid_payment');
  end if;

  if p_checkout_request_id is not null then
    select * into v_attempt
    from public.migration_arrears_payment_attempts
    where checkout_request_id = p_checkout_request_id
    for update;

    if not found then
      return jsonb_build_object('applied', false, 'reason', 'attempt_not_found');
    end if;
    if v_attempt.status = 'success' then
      return jsonb_build_object('applied', false, 'duplicate', true);
    end if;
    if v_attempt.status <> 'pending' then
      return jsonb_build_object('applied', false, 'reason', v_attempt.status);
    end if;
    if p_amount <> v_attempt.requested_amount or p_assignment_id <> v_attempt.assignment_id
      or p_recorded_by <> v_attempt.tenant_id
      or (p_phone_number is not null and p_phone_number <> v_attempt.phone_number) then
      update public.migration_arrears_payment_attempts
      set status = 'needs_review', result_description = 'Callback details did not match the payment request.', processed_at = now()
      where checkout_request_id = p_checkout_request_id;
      return jsonb_build_object('applied', false, 'reason', 'callback_mismatch');
    end if;
  end if;

  select * into v_assignment
  from public.unit_assignments
  where id = p_assignment_id and status = 'active'
  for update;

  if not found or not v_assignment.is_migrated
    or (p_checkout_request_id is not null and v_assignment.tenant_id <> p_recorded_by) then
    if p_checkout_request_id is not null then
      update public.migration_arrears_payment_attempts
      set status = 'needs_review', result_description = 'The tenant assignment is no longer active.', processed_at = now()
      where checkout_request_id = p_checkout_request_id;
    end if;
    return jsonb_build_object('applied', false, 'reason', 'assignment_not_found');
  end if;

  select first_online_billing_month into v_first_month
  from public.billing_settings where id = true;

  if v_first_month is not null then
    select * into v_invoice
    from public.invoices
    where unit_id = v_assignment.unit_id
      and tenant_id = v_assignment.tenant_id
      and billing_month = v_first_month
      and published_at is not null
    for update;
    v_has_published_invoice := found;
  end if;

  if v_has_published_invoice then
    if p_amount > coalesce(v_invoice.outstanding_balance, 0) then
      if p_checkout_request_id is not null then
        update public.migration_arrears_payment_attempts
        set status = 'needs_review', result_description = 'Invoice balance changed before callback processing.', processed_at = now()
        where checkout_request_id = p_checkout_request_id;
      end if;
      return jsonb_build_object('applied', false, 'reason', 'invoice_balance_changed');
    end if;
    v_paid := coalesce(v_invoice.amount_paid, 0) + p_amount;
    v_balance := greatest(0, coalesce(v_invoice.outstanding_balance, 0) - p_amount);
    v_status := case when v_balance = 0 then 'paid' else 'partial' end;
    update public.invoices
    set amount_paid = v_paid, outstanding_balance = v_balance, status = v_status, updated_at = now()
    where id = v_invoice.id;
    v_remaining := coalesce(v_assignment.opening_arrears, 0);
  else
    if p_amount > coalesce(v_assignment.opening_arrears, 0) then
      if p_checkout_request_id is not null then
        update public.migration_arrears_payment_attempts
        set status = 'needs_review', result_description = 'Arrears changed before callback processing.', processed_at = now()
        where checkout_request_id = p_checkout_request_id;
      end if;
      return jsonb_build_object('applied', false, 'reason', 'arrears_changed');
    end if;

    v_remaining := coalesce(v_assignment.opening_arrears, 0) - p_amount;
    update public.unit_assignments
    set opening_arrears = v_remaining, updated_at = now()
    where id = v_assignment.id;

    if v_first_month is not null then
      update public.invoices
      set carried_arrears_amount = v_remaining,
          total_amount_due = coalesce(rent_amount, 0) + coalesce(garbage_amount, 0) + coalesce(water_amount, 0) + v_remaining,
          outstanding_balance = greatest(0, coalesce(rent_amount, 0) + coalesce(garbage_amount, 0) + coalesce(water_amount, 0) + v_remaining - coalesce(amount_paid, 0)),
          updated_at = now()
      where unit_id = v_assignment.unit_id
        and tenant_id = v_assignment.tenant_id
        and billing_month = v_first_month
        and published_at is null;
    end if;
  end if;

  insert into public.migration_arrears_payments(
    assignment_id, amount, mpesa_reference, payment_date, recorded_by, method, checkout_request_id
  ) values (
    v_assignment.id,
    p_amount,
    coalesce(p_mpesa_receipt, p_reference),
    coalesce(p_payment_date, current_date),
    p_recorded_by,
    p_method,
    p_checkout_request_id
  );

  if p_checkout_request_id is not null then
    update public.migration_arrears_payment_attempts
    set status = 'success', result_description = null, processed_at = now()
    where checkout_request_id = p_checkout_request_id;
  end if;

  return jsonb_build_object('applied', true, 'remaining_arrears', v_remaining, 'invoice_balance', v_balance);
end;
$$;

revoke all on function public.record_migration_arrears_payment(uuid, numeric, text, date, uuid, text, text, text, text, text) from public, anon, authenticated;
grant execute on function public.record_migration_arrears_payment(uuid, numeric, text, date, uuid, text, text, text, text, text) to service_role;