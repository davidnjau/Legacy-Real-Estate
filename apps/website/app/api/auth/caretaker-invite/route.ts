import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';
import { getActivePropertyId } from '../../../../lib/property-access';

const supabaseAdmin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } }
);

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character]!);
}

async function sendInviteEmail(email: string, actionLink: string) {
  const apiKey = process.env.BREVO_API_KEY;
  const senderEmail = process.env.BREVO_SENDER_EMAIL;
  if (!apiKey || !senderEmail) {
    throw new Error('A configured email provider is required to send this activation link.');
  }

  const senderName = process.env.BREVO_SENDER_NAME || 'Property Management';
  const response = await fetch('https://api.brevo.com/v3/smtp/email', {
    method: 'POST',
    headers: { accept: 'application/json', 'api-key': apiKey, 'content-type': 'application/json' },
    body: JSON.stringify({
      sender: { email: senderEmail, name: senderName },
      to: [{ email }],
      subject: `Activate your ${senderName} caretaker account`,
      htmlContent: `<p>You have been invited to manage a property as a caretaker.</p><p><a href="${escapeHtml(actionLink)}">Activate your account</a></p><p>This link expires in 48 hours.</p>`,
    }),
  });
  if (!response.ok) throw new Error(`Email provider returned ${response.status}: ${await response.text()}`);
}

export async function POST(request: Request) {
  try {
    const auth = await requireApiRole(['landlord']);
    if (auth.response) return auth.response;
    const landlordId = auth.currentUser!.user.id;
    const scope = await getActivePropertyId(supabaseAdmin, landlordId);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

    const body = await request.json();
    const email = String(body.email || '').trim().toLowerCase();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      return NextResponse.json({ error: 'A valid caretaker email address is required.' }, { status: 400 });
    }

    const { data: existingProfile, error: profileError } = await supabaseAdmin
      .from('users').select('id').ilike('email', email).maybeSingle();
    if (profileError) return NextResponse.json({ error: profileError.message }, { status: 500 });
    if (existingProfile) return NextResponse.json({ error: 'This email already belongs to an account.' }, { status: 409 });

    const { data: pendingInvite, error: pendingError } = await supabaseAdmin.from('caretaker_invites')
      .select('id, expires_at')
      .eq('email', email)
      .eq('property_id', scope.propertyId)
      .eq('status', 'active')
      .order('created_at', { ascending: false })
      .limit(1)
      .maybeSingle();
    if (pendingError) return NextResponse.json({ error: pendingError.message }, { status: 500 });
    if (pendingInvite && new Date(pendingInvite.expires_at) > new Date()) {
      return NextResponse.json({ error: 'An active invitation already exists for this email. Use Resend from the invitation list.' }, { status: 409 });
    }
    if (pendingInvite) {
      const { error } = await supabaseAdmin.from('caretaker_invites').update({ status: 'expired' }).eq('id', pendingInvite.id);
      if (error) return NextResponse.json({ error: error.message }, { status: 500 });
    }

    const { data: previousInvite, error: previousError } = await supabaseAdmin.from('caretaker_invites')
      .select('auth_user_id')
      .eq('email', email)
      .eq('property_id', scope.propertyId)
      .not('auth_user_id', 'is', null)
      .order('created_at', { ascending: false })
      .limit(1)
      .maybeSingle();
    if (previousError) return NextResponse.json({ error: previousError.message }, { status: 500 });
    const previousAuthUserId = previousInvite?.auth_user_id || null;

    const inviteId = crypto.randomUUID();
    const expiresAt = new Date(Date.now() + 48 * 60 * 60 * 1000).toISOString();
    const { data: invite, error: insertError } = await supabaseAdmin.from('caretaker_invites')
      .insert({
        id: inviteId,
        code: crypto.randomUUID(),
        email,
        created_by: landlordId,
        property_id: scope.propertyId,
        expires_at: expiresAt,
        status: 'active',
      })
      .select('id, expires_at')
      .single();
    if (insertError || !invite) {
      if (insertError?.code === '23505') {
        return NextResponse.json({ error: 'An active invitation already exists for this email.' }, { status: 409 });
      }
      return NextResponse.json({ error: insertError?.message || 'Failed to create caretaker invitation.' }, { status: 500 });
    }

    const origin = new URL(request.url).origin;
    const redirectTo = `${origin}/caretaker-signup?inviteId=${encodeURIComponent(invite.id)}`;
    let invitedUserId: string | undefined = previousAuthUserId || undefined;
    try {
      const apiKey = process.env.BREVO_API_KEY;
      const senderEmail = process.env.BREVO_SENDER_EMAIL;
      if (apiKey && senderEmail) {
        const { data, error } = await supabaseAdmin.auth.admin.generateLink({
          type: previousAuthUserId ? 'recovery' : 'invite',
          email,
          options: {
            redirectTo,
            ...(!previousAuthUserId && { data: { role: 'caretaker', property_id: scope.propertyId, caretaker_invite_id: invite.id } }),
          },
        });
        if (error || !data.properties?.action_link) throw new Error(error?.message || 'Authentication provider did not return an invite link.');
        invitedUserId = data.user?.id || invitedUserId;
        await sendInviteEmail(email, data.properties.action_link);
      } else {
        if (previousAuthUserId) {
          const { error } = await supabaseAdmin.auth.resetPasswordForEmail(email, { redirectTo });
          if (error) throw new Error(error.message);
        } else {
          const { data, error } = await supabaseAdmin.auth.admin.inviteUserByEmail(email, {
            redirectTo,
            data: { role: 'caretaker', property_id: scope.propertyId, caretaker_invite_id: invite.id },
          });
          if (error) throw new Error(error.message);
          invitedUserId = data.user?.id;
        }
      }
      if (invitedUserId) {
        const { error } = await supabaseAdmin.from('caretaker_invites').update({ auth_user_id: invitedUserId }).eq('id', invite.id);
        if (error) throw new Error(`Activation email sent, but invitation tracking failed: ${error.message}`);
      }
    } catch (error) {
      if (invitedUserId && !previousAuthUserId) {
        const { error: authDeleteError } = await supabaseAdmin.auth.admin.deleteUser(invitedUserId);
        if (authDeleteError) console.error('Failed to remove caretaker auth user after email delivery failure:', authDeleteError);
      }
      const { error: deleteError } = await supabaseAdmin.from('caretaker_invites').delete().eq('id', invite.id);
      if (deleteError) console.error('Failed to remove unsent caretaker invitation:', deleteError);
      return NextResponse.json({
        error: `Unable to send caretaker activation email: ${error instanceof Error ? error.message : 'Email provider error.'}`,
      }, { status: 502 });
    }

    return NextResponse.json({ success: true, email, expiresAt: invite.expires_at });
  } catch (error) {
    console.error('Caretaker invitation failed:', error);
    return NextResponse.json({ error: error instanceof Error ? error.message : 'Caretaker invitation failed.' }, { status: 500 });
  }
}

export async function GET() {
  try {
    const auth = await requireApiRole(['landlord']);
    if (auth.response) return auth.response;
    const landlordId = auth.currentUser!.user.id;
    const scope = await getActivePropertyId(supabaseAdmin, landlordId);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

    const { error: expiryError } = await supabaseAdmin.from('caretaker_invites')
      .update({ status: 'expired' })
      .eq('created_by', landlordId)
      .eq('property_id', scope.propertyId)
      .eq('status', 'active')
      .lt('expires_at', new Date().toISOString());
    if (expiryError) return NextResponse.json({ error: expiryError.message }, { status: 500 });

    const { data: invites, error } = await supabaseAdmin.from('caretaker_invites')
      .select('id, email, expires_at, status, used_at')
      .eq('created_by', landlordId)
      .eq('property_id', scope.propertyId)
      .order('created_at', { ascending: false })
      .limit(20);
    if (error) return NextResponse.json({ error: error.message }, { status: 500 });

    const { data: memberships, error: memberError } = await supabaseAdmin.from('property_members')
      .select('user_id')
      .eq('property_id', scope.propertyId)
      .eq('member_role', 'caretaker');
    if (memberError) return NextResponse.json({ error: memberError.message }, { status: 500 });
    const caretakerIds = (memberships || []).map((membership) => membership.user_id);
    const { data: caretakers, error: caretakerError } = caretakerIds.length
      ? await supabaseAdmin.from('users').select('id, full_name, email, phone, created_at').in('id', caretakerIds)
      : { data: [], error: null };
    if (caretakerError) return NextResponse.json({ error: caretakerError.message }, { status: 500 });
    return NextResponse.json({ invites: invites || [], caretakers: caretakers || [] });
  } catch (error) {
    console.error('Caretaker invitation lookup failed:', error);
    return NextResponse.json({ error: error instanceof Error ? error.message : 'Failed to load caretaker invitations.' }, { status: 500 });
  }
}

export async function DELETE(request: Request) {
  try {
    const auth = await requireApiRole(['landlord']);
    if (auth.response) return auth.response;
    const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });
    const { caretakerId } = await request.json();
    if (typeof caretakerId !== 'string' || !caretakerId) {
      return NextResponse.json({ error: 'Caretaker ID is required.' }, { status: 400 });
    }
    const { error } = await supabaseAdmin.from('property_members').delete()
      .eq('property_id', scope.propertyId).eq('user_id', caretakerId).eq('member_role', 'caretaker');
    if (error) return NextResponse.json({ error: error.message }, { status: 500 });
    return NextResponse.json({ success: true, message: 'Caretaker property access removed.' });
  } catch (error) {
    console.error('Caretaker removal failed:', error);
    return NextResponse.json({ error: error instanceof Error ? error.message : 'Failed to remove caretaker.' }, { status: 500 });
  }
}

export async function PUT(request: Request) {
  try {
    const auth = await requireApiRole(['landlord']);
    if (auth.response) return auth.response;
    const landlordId = auth.currentUser!.user.id;
    const scope = await getActivePropertyId(supabaseAdmin, landlordId);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });
    const { inviteId } = await request.json();
    if (typeof inviteId !== 'string' || !inviteId) return NextResponse.json({ error: 'Invite ID is required.' }, { status: 400 });
    const { data: invite, error } = await supabaseAdmin.from('caretaker_invites')
      .select('id, email, expires_at, status')
      .eq('id', inviteId).eq('created_by', landlordId).eq('property_id', scope.propertyId).maybeSingle();
    if (error) return NextResponse.json({ error: error.message }, { status: 500 });
    if (!invite?.email || invite.status !== 'active') return NextResponse.json({ error: 'Active email invitation not found.' }, { status: 404 });
    if (new Date(invite.expires_at) <= new Date()) return NextResponse.json({ error: 'This invitation expired. Create a new one.' }, { status: 410 });

    const origin = new URL(request.url).origin;
    const redirectTo = `${origin}/caretaker-signup?inviteId=${encodeURIComponent(invite.id)}`;
    const { data, error: linkError } = await supabaseAdmin.auth.admin.generateLink({
      type: 'recovery',
      email: invite.email,
      options: { redirectTo },
    });
    if (linkError || !data.properties?.action_link) {
      return NextResponse.json({ error: linkError?.message || 'Authentication provider did not return a new activation link.' }, { status: 502 });
    }
    const apiKey = process.env.BREVO_API_KEY;
    const senderEmail = process.env.BREVO_SENDER_EMAIL;
    if (apiKey && senderEmail) {
      try {
        await sendInviteEmail(invite.email, data.properties.action_link);
      } catch (sendError) {
        return NextResponse.json({ error: `Unable to resend activation email: ${sendError instanceof Error ? sendError.message : 'Email provider error.'}` }, { status: 502 });
      }
    } else {
      const { error: emailError } = await supabaseAdmin.auth.resetPasswordForEmail(invite.email, { redirectTo });
      if (emailError) return NextResponse.json({ error: `Unable to resend activation email: ${emailError.message}` }, { status: 502 });
    }
    return NextResponse.json({ success: true, message: `Activation email resent to ${invite.email}.` });
  } catch (error) {
    console.error('Caretaker invite resend failed:', error);
    return NextResponse.json({ error: error instanceof Error ? error.message : 'Failed to resend caretaker invitation.' }, { status: 500 });
  }
}

export async function PATCH(request: Request) {
  try {
    const authorization = request.headers.get('authorization') || '';
    const token = authorization.startsWith('Bearer ') ? authorization.slice(7) : '';
    if (!token) return NextResponse.json({ error: 'A valid caretaker activation session is required.' }, { status: 401 });

    const { data: authData, error: authError } = await supabaseAdmin.auth.getUser(token);
    const user = authData.user;
    if (authError || !user?.email) return NextResponse.json({ error: 'Invalid activation session.' }, { status: 401 });

    const body = await request.json();
    const inviteId = String(body.inviteId || '');
    const fullName = String(body.fullName || '').trim();
    const phone = String(body.phone || '').trim();
    if (!inviteId || !fullName || !phone) return NextResponse.json({ error: 'Invite, full name, and phone are required.' }, { status: 400 });

    const { data: invite, error: inviteError } = await supabaseAdmin.from('caretaker_invites')
      .select('id, email, property_id, expires_at, status')
      .eq('id', inviteId).maybeSingle();
    if (inviteError) return NextResponse.json({ error: inviteError.message }, { status: 500 });
    if (!invite || invite.status !== 'active' || !invite.email || !invite.property_id) {
      return NextResponse.json({ error: 'This caretaker invitation is no longer active.' }, { status: 410 });
    }
    if (new Date(invite.expires_at) <= new Date()) {
      await supabaseAdmin.from('caretaker_invites').update({ status: 'expired' }).eq('id', invite.id);
      return NextResponse.json({ error: 'This caretaker invitation has expired.' }, { status: 410 });
    }
    if (user.email.toLowerCase() !== invite.email.toLowerCase()) {
      return NextResponse.json({ error: 'This activation link is not for the signed-in email address.' }, { status: 403 });
    }

    const { error: profileError } = await supabaseAdmin.from('users').upsert({
      id: user.id,
      full_name: fullName,
      email: invite.email,
      phone,
      role: 'caretaker',
      status: 'active',
      active_property_id: invite.property_id,
      updated_at: new Date().toISOString(),
    }, { onConflict: 'id' });
    if (profileError) return NextResponse.json({ error: profileError.message }, { status: 500 });

    const { error: membershipError } = await supabaseAdmin.from('property_members').upsert({
      property_id: invite.property_id,
      user_id: user.id,
      member_role: 'caretaker',
    }, { onConflict: 'property_id,user_id' });
    if (membershipError) return NextResponse.json({ error: membershipError.message }, { status: 500 });

    const { error: usedError } = await supabaseAdmin.from('caretaker_invites').update({
      used_by: user.id,
      used_at: new Date().toISOString(),
      status: 'used',
    }).eq('id', invite.id).eq('status', 'active');
    if (usedError) return NextResponse.json({ error: usedError.message }, { status: 500 });
    return NextResponse.json({ success: true });
  } catch (error) {
    console.error('Caretaker activation failed:', error);
    return NextResponse.json({ error: error instanceof Error ? error.message : 'Caretaker activation failed.' }, { status: 500 });
  }
}
