import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';

const supabaseAdmin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
      detectSessionInUrl: false,
    },
  }
);

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const fullName = String(body.fullName || '').trim();
    const email = String(body.email || '').trim().toLowerCase();
    const phone = String(body.phone || '').trim();
    const claimCode = String(body.claimCode || '').trim().toUpperCase();
    const password = String(body.password || '');

    if (!fullName || !email || !phone || !claimCode || !password) {
      return NextResponse.json(
        { error: 'All fields are required.' },
        { status: 400 }
      );
    }

    if (password.length < 6) {
      return NextResponse.json(
        { error: 'Password must be at least 6 characters long.' },
        { status: 400 }
      );
    }

    const { data: existingClaim, error: claimLookupError } = await supabaseAdmin
      .from('claim_codes')
      .select('*')
      .eq('code', claimCode)
      .maybeSingle();

    if (claimLookupError) {
      return NextResponse.json(
        { error: 'Failed to validate unit claim code.' },
        { status: 500 }
      );
    }

    if (!existingClaim) {
      return NextResponse.json(
        { error: 'Invalid unit claim code.' },
        { status: 400 }
      );
    }

    if (new Date(existingClaim.expires_at) < new Date()) {
      return NextResponse.json(
        { error: 'This unit claim code has expired.' },
        { status: 410 }
      );
    }

    if (existingClaim.used_by_user_id) {
      return NextResponse.json(
        { error: 'This unit claim code has already been used.' },
        { status: 409 }
      );
    }

    const { data: settlement } = await supabaseAdmin
      .from('move_in_settlements')
      .select('id, tenant_id, tenant_name, tenant_phone, status, activation_override, outstanding_balance')
      .eq('unit_id', existingClaim.unit_id)
      .in('status', ['paid', 'move_in_pending'])
      .order('created_at', { ascending: false })
      .limit(1)
      .maybeSingle();

    if (!settlement && !fullName) {
      return NextResponse.json({ error: 'This new tenant requires a completed move-in settlement before registration.' }, { status: 400 });
    }

    if (settlement && settlement.status !== 'paid' && !settlement.activation_override) {
      return NextResponse.json({ error: 'The move-in settlement is not fully cleared.' }, { status: 400 });
    }

    const resolvedFullName = fullName;
    const resolvedPhone = phone;

    // Check if the user already exists as a dormant migrated account
    const { data: existingProfile } = await supabaseAdmin
      .from('users')
      .select('id, status')
      .eq('email', email)
      .maybeSingle();

    let userId: string;
let today = new Date().toISOString().slice(0,10);

    if (existingProfile && existingProfile.status === 'dormant') {
      // Activate dormant user
      const { error: updateAuthError } = await supabaseAdmin.auth.admin.updateUserById(existingProfile.id, {
        password,
        user_metadata: { full_name: resolvedFullName, phone: resolvedPhone },
      });

      if (updateAuthError) {
        return NextResponse.json({ error: 'Failed to set password for pre-registered account.' }, { status: 500 });
      }

      await supabaseAdmin.from('users').update({ status: 'active', full_name: resolvedFullName, phone: resolvedPhone }).eq('id', existingProfile.id);
      userId = existingProfile.id;

    } else {
      // Normal creation flow
      const { data: authUser, error: createAuthError } = await supabaseAdmin.auth.admin.createUser({
        email,
        password,
        email_confirm: true,
        user_metadata: {
          full_name: fullName,
          phone,
        },
      });

      if (createAuthError || !authUser?.user) {
        return NextResponse.json(
          { error: createAuthError?.message || 'Unable to create tenant account.' },
          { status: 400 }
        );
      }
      
      userId = authUser.user.id;

      const { error: profileInsertError } = await supabaseAdmin.from('users').insert({
        id: userId,
          full_name: resolvedFullName,
        email,
        phone: resolvedPhone,
        role: 'tenant',
        status: 'active',
        password_hash: null,
      });

      if (profileInsertError) {
        await supabaseAdmin.auth.admin.deleteUser(userId);
        return NextResponse.json(
          { error: 'Failed to create tenant profile in the database.' },
          { status: 500 }
        );
      }

      const today = new Date().toISOString().slice(0, 10);

      const { error: assignmentError } = await supabaseAdmin.from('unit_assignments').insert({
        unit_id: existingClaim.unit_id,
        tenant_id: userId,
        move_in_date: today,
        status: 'active',
        deposit_paid: 0,
      });

      if (assignmentError) {
        await supabaseAdmin.auth.admin.deleteUser(userId);
        await supabaseAdmin.from('users').delete().eq('id', userId);
        return NextResponse.json(
          { error: 'Failed to link the tenant to the unit.' },
          { status: 500 }
        );
      }

const { error: unitUpdateError } = await supabaseAdmin
         .from('units')
         .update({ status: 'occupied', updated_at: new Date().toISOString() })
         .eq('id', existingClaim.unit_id);

       if (unitUpdateError) {
         await supabaseAdmin.auth.admin.deleteUser(userId);
         await supabaseAdmin.from('users').delete().eq('id', userId);
         await supabaseAdmin.from('unit_assignments').delete().eq('tenant_id', userId);
         return NextResponse.json(
           { error: 'Failed to update unit occupancy.' },
           { status: 500 }
         );
       }

       // Ensure baseline reading exists for the unit
       const { count: baselineCount, error: baselineCountError } = await supabaseAdmin
         .from('water_readings')
         .select('id', { count: 'exact', head: true })
         .eq('unit_id', existingClaim.unit_id)
         .eq('reading_type', 'baseline');

       if (!baselineCountError && baselineCount === 0) {
         const { error: baselineInsertError } = await supabaseAdmin
           .from('water_readings')
           .insert({
             unit_id: existingClaim.unit_id,
             tenant_id: userId,
             reading_date: today,
             meter_reading: 0,
             reading_type: 'baseline',
             status: 'submitted',
           });

         if (baselineInsertError) {
           console.error('Failed to insert baseline reading:', baselineInsertError);
           // Continue anyway
         }
       }

    }

    if (settlement) {
      await supabaseAdmin.from('move_in_settlements').update({ tenant_id: userId, tenant_name: resolvedFullName, tenant_phone: resolvedPhone, status: 'active', activated_at: new Date().toISOString(), updated_at: new Date().toISOString() }).eq('id', settlement.id);
    }

    const { error: codeUpdateError } = await supabaseAdmin
      .from('claim_codes')
      .update({
        used_by_user_id: userId,
        used_at: new Date().toISOString(),
      })
      .eq('id', existingClaim.id);

    if (codeUpdateError) {
      await supabaseAdmin.auth.admin.deleteUser(userId);
      await supabaseAdmin.from('users').delete().eq('id', userId);
      await supabaseAdmin.from('unit_assignments').delete().eq('tenant_id', userId);
      await supabaseAdmin.from('units').update({ status: 'vacant', updated_at: new Date().toISOString() }).eq('id', existingClaim.unit_id);
      return NextResponse.json(
        { error: 'Failed to finalize the claim code.' },
        { status: 500 }
      );
    }

    return NextResponse.json({
      success: true,
      message: 'Tenant account created successfully.',
      user: {
        id: userId,
        email,
      },
    });
  } catch (error: any) {
    console.error('Tenant registration failed:', error);
    return NextResponse.json(
      { error: error?.message || 'An unexpected error occurred while creating the tenant account.' },
      { status: 500 }
    );
  }
}
