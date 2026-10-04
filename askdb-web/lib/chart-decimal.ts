export type DecimalParts = { coefficient: bigint; scale: number };

function powerOfTen(exponent: number) {
  return BigInt(`1${"0".repeat(exponent)}`);
}

export function decimalParts(value: unknown): DecimalParts | undefined {
  const text =
    typeof value === "number" && Number.isFinite(value)
      ? String(value)
      : typeof value === "string"
        ? value.trim()
        : "";
  if (!text || text.length > 256) return undefined;
  const match = /^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(text);
  if (!match) return undefined;
  const exponent = Number(match[4] ?? "0");
  if (!Number.isInteger(exponent) || Math.abs(exponent) > 256) return undefined;
  const fraction = match[3] ?? "";
  let digits = `${match[2]}${fraction}`.replace(/^0+/, "") || "0";
  let scale = fraction.length - exponent;
  if (scale < 0) {
    digits += "0".repeat(-scale);
    scale = 0;
  }
  while (scale > 0 && digits.endsWith("0")) {
    digits = digits.slice(0, -1);
    scale -= 1;
  }
  if (digits === "0") return { coefficient: BigInt("0"), scale: 0 };
  const sign = match[1] === "-" ? "-" : "";
  return { coefficient: BigInt(`${sign}${digits}`), scale };
}

export function compareDecimalParts(left: DecimalParts, right: DecimalParts) {
  const scale = Math.max(left.scale, right.scale);
  const leftValue = left.coefficient * powerOfTen(scale - left.scale);
  const rightValue = right.coefficient * powerOfTen(scale - right.scale);
  return leftValue < rightValue ? -1 : leftValue > rightValue ? 1 : 0;
}

export function compareDecimalValues(left: unknown, right: unknown) {
  const leftParts = decimalParts(left);
  const rightParts = decimalParts(right);
  return leftParts && rightParts ? compareDecimalParts(leftParts, rightParts) : undefined;
}

export function sumDecimalValues(values: unknown[]): DecimalParts | undefined {
  const parsed = values.map(decimalParts);
  if (parsed.some((value) => !value)) return undefined;
  const parts = parsed as DecimalParts[];
  const scale = Math.max(0, ...parts.map((value) => value.scale));
  const coefficient = parts.reduce(
    (sum, value) => sum + value.coefficient * powerOfTen(scale - value.scale),
    BigInt("0"),
  );
  return { coefficient, scale };
}

export function decimalText(parts: DecimalParts) {
  const negative = parts.coefficient < BigInt("0");
  const digits = (negative ? -parts.coefficient : parts.coefficient).toString();
  const padded = digits.padStart(parts.scale + 1, "0");
  const whole = parts.scale ? padded.slice(0, -parts.scale) : padded;
  const fraction = parts.scale ? padded.slice(-parts.scale).replace(/0+$/, "") : "";
  return `${negative && parts.coefficient !== BigInt("0") ? "-" : ""}${whole}${fraction ? `.${fraction}` : ""}`;
}

function groupedInteger(value: string) {
  return BigInt(value || "0").toLocaleString("zh-CN");
}

export function formatDecimal(
  value: unknown,
  shift: number,
  decimalPlaces: "auto" | number,
): string {
  const parts = decimalParts(value);
  if (!parts) return "—";
  let coefficient = parts.coefficient;
  let scale = parts.scale - shift;
  if (scale < 0) {
    coefficient *= powerOfTen(-scale);
    scale = 0;
  }
  const negative = coefficient < BigInt("0");
  let absolute = negative ? -coefficient : coefficient;
  if (decimalPlaces !== "auto") {
    if (scale > decimalPlaces) {
      const divisor = powerOfTen(scale - decimalPlaces);
      const remainder = absolute % divisor;
      absolute /= divisor;
      if (remainder * BigInt("2") >= divisor) absolute += BigInt("1");
    } else if (scale < decimalPlaces) {
      absolute *= powerOfTen(decimalPlaces - scale);
    }
    scale = decimalPlaces;
  }
  const digits = absolute.toString().padStart(scale + 1, "0");
  const whole = scale ? digits.slice(0, -scale) : digits;
  const fraction = scale ? digits.slice(-scale) : "";
  const sign = negative && absolute !== BigInt("0") ? "-" : "";
  return `${sign}${groupedInteger(whole)}${fraction ? `.${fraction}` : ""}`;
}

export function exactPlotNumber(value: unknown): number | undefined {
  const parsed = decimalParts(value);
  if (!parsed) return undefined;
  const number = Number(decimalText(parsed));
  if (!Number.isFinite(number)) return undefined;
  const roundTrip = decimalParts(number);
  return roundTrip && compareDecimalParts(parsed, roundTrip) === 0 ? number : undefined;
}

export function exactPercentShare(value: unknown, total: DecimalParts, decimalPlaces = 2) {
  const numeratorParts = decimalParts(value);
  if (!numeratorParts || total.coefficient <= BigInt("0")) return "0";
  let numerator = numeratorParts.coefficient;
  let denominator = total.coefficient;
  const scaleDifference = total.scale - numeratorParts.scale;
  if (scaleDifference > 0) numerator *= powerOfTen(scaleDifference);
  else if (scaleDifference < 0) denominator *= powerOfTen(-scaleDifference);
  numerator *= BigInt("100") * powerOfTen(decimalPlaces);
  const remainder = numerator % denominator;
  let rounded = numerator / denominator;
  if (remainder * BigInt("2") >= denominator) rounded += BigInt("1");
  const digits = rounded.toString().padStart(decimalPlaces + 1, "0");
  const whole = decimalPlaces ? digits.slice(0, -decimalPlaces) : digits;
  const fraction = decimalPlaces ? digits.slice(-decimalPlaces) : "";
  return `${groupedInteger(whole)}${fraction ? `.${fraction}` : ""}`;
}
