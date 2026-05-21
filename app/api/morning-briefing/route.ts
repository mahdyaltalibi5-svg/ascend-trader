import { NextResponse } from "next/server";
import { createServiceClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const supabase = await createServiceClient();
    const { data, error } = await supabase
      .from("morning_briefings")
      .select("*")
      .order("generated_at", { ascending: false })
      .limit(1)
      .single();

    if (error || !data) {
      return NextResponse.json({ briefing: null }, { status: 200 });
    }

    return NextResponse.json({ briefing: data });
  } catch {
    return NextResponse.json({ error: "Failed to fetch briefing" }, { status: 500 });
  }
}
