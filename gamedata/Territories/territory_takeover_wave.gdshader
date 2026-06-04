shader_type canvas_item;

uniform vec4 from_color : source_color = vec4(0.5, 0.5, 0.5, 1.0);
uniform vec4 to_color : source_color = vec4(1.0, 1.0, 1.0, 1.0);
uniform float progress : hint_range(0.0, 1.0) = 0.0;
uniform float wave_width : hint_range(0.01, 1.0) = 0.22;
uniform float edge_softness : hint_range(0.001, 0.5) = 0.08;
uniform vec2 wave_direction = vec2(1.0, 0.0);
uniform float flat_strength : hint_range(0.0, 1.0) = 0.30;

void fragment() {
	vec4 tex = texture(TEXTURE, UV);
	vec2 dir = normalize(wave_direction);
	if (length(wave_direction) < 0.001) {
		dir = vec2(1.0, 0.0);
	}

	// Project UV onto direction and remap to [0..1] so sweep works in any direction.
	float projected = dot(UV, dir);
	float projected_min = min(0.0, dir.x) + min(0.0, dir.y);
	float projected_max = max(0.0, dir.x) + max(0.0, dir.y);
	float projected_range = max(0.0001, projected_max - projected_min);
	float t = clamp((projected - projected_min) / projected_range, 0.0, 1.0);

	float edge_step = step(t, progress);
	vec3 wipe_rgb = mix(from_color.rgb, to_color.rgb, edge_step);
	vec3 tinted_rgb = tex.rgb * wipe_rgb;
	vec3 final_rgb = mix(tinted_rgb, wipe_rgb, flat_strength);
	float final_a = mix(from_color.a, to_color.a, edge_step);
	COLOR = vec4(final_rgb, tex.a * final_a);
}
