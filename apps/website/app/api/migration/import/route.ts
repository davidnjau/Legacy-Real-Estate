export async function POST(request: Request) {
  return Response.json(
    { error: 'Bulk import now requires the Property Officer Imports page and a CSV/XLSX template.' },
    { status: 410 },
  );
}
