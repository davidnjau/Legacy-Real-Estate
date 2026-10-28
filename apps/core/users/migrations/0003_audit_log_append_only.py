from django.db import migrations


FORWARD_SQL = """
CREATE FUNCTION users_auditlog_immutable() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND OLD.user_id IS NOT NULL
       AND NEW.user_id IS NULL
       AND (to_jsonb(NEW) - 'user_id') IS NOT DISTINCT FROM
           (to_jsonb(OLD) - 'user_id') THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'Audit log entries are immutable';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER users_auditlog_immutable
BEFORE UPDATE OR DELETE ON users_auditlog
FOR EACH ROW EXECUTE FUNCTION users_auditlog_immutable();
"""

REVERSE_SQL = """
DROP TRIGGER users_auditlog_immutable ON users_auditlog;
DROP FUNCTION users_auditlog_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [("users", "0002_userprofile_failed_login_attempts_and_more")]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
