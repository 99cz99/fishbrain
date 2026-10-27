import 'package:flutter/cupertino.dart' show CupertinoPageTransitionsBuilder;
import 'package:flutter/material.dart';

/// 全局主题模式（浅色/深色/跟随系统）。设定页改它，main.dart 监听它。
final ValueNotifier<ThemeMode> themeNotifier = ValueNotifier(ThemeMode.system);

/// 设计令牌：颜色。现代 iOS 视觉语言——干净的中性背景 + 单一强调色，
/// 语义色（成功/警告/危险）用于状态，不参与日常配色。
abstract final class AppColors {
  // 强调色（iOS 系统蓝系）
  static const Color primaryLight = Color(0xFF007AFF);
  static const Color primaryDark = Color(0xFF0A84FF);

  // 中性背景与表面
  static const Color backgroundLight = Color(0xFFF2F2F7); // 分组灰
  static const Color backgroundDark = Color(
    0xFF0A0A0F,
  ); // 深空蓝黑（Liquid Glass 基底）
  static const Color surfaceLight = Color(0xFFFFFFFF);
  static const Color surfaceDark = Color(0xFF1C1C1E); // 抬高表面
  static const Color surface2Dark = Color(0xFF2C2C2E);

  // 文本
  static const Color textLight = Color(0xFF1C1C1E);
  static const Color textDark = Color(0xFFFFFFFF);
  static const Color textSecondaryLight = Color(0xFF6D6D72);
  static const Color textSecondaryDark = Color(0xFFAEAEB2);

  // 边框 / 填充
  static const Color borderLight = Color(0xFFE5E5EA);
  static const Color borderDark = Color(0xFF38383A);
  static const Color fillLight = Color(0xFFE9E9EB); // 输入框 / 分段控件底
  static const Color fillDark = Color(0xFF2C2C2E);

  // Liquid Glass 材质：半透明表面 + 细腻边缘高光
  static const Color glassSurfaceDark = Color(0xB01C1C1E); // 深色玻璃表面 69%
  static const Color glassSurfaceLight = Color(0xB3FFFFFF); // 浅色玻璃表面 70%
  static const Color glassHighlightDark = Color(0x1FFFFFFF); // 深色边缘高光（白 12%）
  static const Color glassHighlightLight = Color(0xFFFFFFFF); // 浅色边缘高光

  // 语义色（iOS 系统色）
  static const Color success = Color(0xFF34C759);
  static const Color warning = Color(0xFFFF9F0A);
  static const Color danger = Color(0xFFFF3B30);
  static const Color info = Color(0xFF007AFF);
}

/// 设计令牌：圆角。
abstract final class AppRadius {
  static const double card = 14;
  static const double field = 12;
  static const double button = 12;
  static const double pill = 999; // 胶囊
  static const double sheet = 20;
}

/// 统一主题。所有页面共用这一套，避免逐页硬编码颜色/间距。
abstract final class AppTheme {
  static ThemeData get light => _build(Brightness.light);
  static ThemeData get dark => _build(Brightness.dark);

  /// Liquid Glass 表面色（按亮度）。
  static Color glassSurface(Brightness b) => b == Brightness.dark
      ? AppColors.glassSurfaceDark
      : AppColors.glassSurfaceLight;

  /// Liquid Glass 边缘高光（按亮度）。
  static Color glassHighlight(Brightness b) => b == Brightness.dark
      ? AppColors.glassHighlightDark
      : AppColors.borderLight;

  /// Liquid Glass 镜面渐变（顶部略亮→底部），给卡片模拟玻璃反光。
  static LinearGradient glassGradient(Brightness b) => b == Brightness.dark
      ? const LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [Color(0xFF2A2A2E), Color(0xFF1C1C1E)],
        )
      : const LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [Color(0xFFFFFFFF), Color(0xFFF2F2F7)],
        );

  static ColorScheme _scheme(Brightness b) {
    final isLight = b == Brightness.light;
    final primary = isLight ? AppColors.primaryLight : AppColors.primaryDark;
    final scheme = ColorScheme.fromSeed(seedColor: primary, brightness: b);
    return scheme.copyWith(
      primary: primary,
      surface: isLight ? AppColors.surfaceLight : AppColors.surfaceDark,
      onSurface: isLight ? AppColors.textLight : AppColors.textDark,
      onSurfaceVariant: isLight
          ? AppColors.textSecondaryLight
          : AppColors.textSecondaryDark,
      surfaceContainerLowest: isLight
          ? AppColors.surfaceLight
          : AppColors.surfaceDark,
      surfaceContainerLow: isLight
          ? AppColors.backgroundLight
          : AppColors.surface2Dark,
      surfaceContainer: isLight
          ? AppColors.backgroundLight
          : AppColors.surface2Dark,
      surfaceContainerHigh: isLight
          ? AppColors.fillLight
          : AppColors.borderDark,
      surfaceContainerHighest: isLight
          ? AppColors.fillLight
          : AppColors.borderDark,
      outline: isLight ? const Color(0xFFC7C7CC) : const Color(0xFF48484A),
      outlineVariant: isLight ? AppColors.borderLight : AppColors.borderDark,
      error: AppColors.danger,
    );
  }

  static TextTheme _textTheme(Brightness b) {
    final isLight = b == Brightness.light;
    final onSurface = isLight ? AppColors.textLight : AppColors.textDark;
    final onVariant = isLight
        ? AppColors.textSecondaryLight
        : AppColors.textSecondaryDark;
    return TextTheme(
      // iOS 大标题层级
      headlineLarge: TextStyle(
        fontSize: 34,
        fontWeight: FontWeight.w700,
        letterSpacing: -0.4,
        color: onSurface,
        height: 1.2,
      ),
      headlineSmall: TextStyle(
        fontSize: 22,
        fontWeight: FontWeight.w700,
        letterSpacing: -0.3,
        color: onSurface,
        height: 1.3,
      ),
      titleLarge: TextStyle(
        fontSize: 20,
        fontWeight: FontWeight.w700,
        color: onSurface,
        height: 1.3,
      ),
      titleMedium: TextStyle(
        fontSize: 17,
        fontWeight: FontWeight.w600,
        color: onSurface,
        height: 1.3,
      ),
      titleSmall: TextStyle(
        fontSize: 15,
        fontWeight: FontWeight.w600,
        color: onSurface,
        height: 1.3,
      ),
      bodyLarge: TextStyle(
        fontSize: 17,
        fontWeight: FontWeight.w400,
        color: onSurface,
        height: 1.4,
      ),
      bodyMedium: TextStyle(
        fontSize: 15,
        fontWeight: FontWeight.w400,
        color: onSurface,
        height: 1.4,
      ),
      bodySmall: TextStyle(
        fontSize: 13,
        fontWeight: FontWeight.w400,
        color: onVariant,
        height: 1.4,
      ),
      labelLarge: TextStyle(
        fontSize: 17,
        fontWeight: FontWeight.w500,
        color: onSurface,
        height: 1.3,
      ),
      labelMedium: TextStyle(
        fontSize: 13,
        fontWeight: FontWeight.w500,
        color: onVariant,
      ),
      labelSmall: TextStyle(
        fontSize: 12,
        fontWeight: FontWeight.w500,
        color: onVariant,
      ),
    );
  }

  static ThemeData _build(Brightness b) {
    final isLight = b == Brightness.light;
    final scheme = _scheme(b);
    final text = _textTheme(b);
    final background = isLight
        ? AppColors.backgroundLight
        : AppColors.backgroundDark;
    final fill = isLight ? AppColors.fillLight : AppColors.surface2Dark;

    return ThemeData(
      useMaterial3: true,
      brightness: b,
      colorScheme: scheme,
      scaffoldBackgroundColor: background,
      textTheme: text,
      splashFactory: InkSparkle.splashFactory,

      appBarTheme: AppBarTheme(
        backgroundColor: AppTheme.glassSurface(b),
        foregroundColor: scheme.onSurface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        titleTextStyle: text.titleLarge,
        iconTheme: IconThemeData(color: scheme.primary, size: 24),
        shape: Border(
          bottom: BorderSide(color: AppTheme.glassHighlight(b), width: 0.5),
        ),
      ),

      // 卡片：白底、轻描边、去投影（iOS 分组卡片）
      cardTheme: CardThemeData(
        color: scheme.surface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadius.card),
          side: BorderSide(color: scheme.outlineVariant, width: 0.5),
        ),
      ),

      dividerTheme: DividerThemeData(
        color: scheme.outlineVariant,
        thickness: 0.5,
        space: 1,
      ),

      // 输入框：填充式圆角（iOS 搜索/输入框）
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: fill,
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 14,
          vertical: 12,
        ),
        labelStyle: text.bodySmall,
        hintStyle: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
        floatingLabelStyle: TextStyle(color: scheme.onSurfaceVariant),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AppRadius.field),
          borderSide: BorderSide.none,
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AppRadius.field),
          borderSide: BorderSide.none,
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AppRadius.field),
          borderSide: BorderSide(color: scheme.primary, width: 1.5),
        ),
      ),

      // 按钮：圆角矩形、舒适触控高度、去投影
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: scheme.primary,
          foregroundColor: Colors.white,
          elevation: 0,
          minimumSize: const Size(0, 44),
          textStyle: text.labelLarge,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AppRadius.button),
          ),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: scheme.primary,
          minimumSize: const Size(0, 44),
          textStyle: text.labelLarge,
          side: BorderSide(color: scheme.outline, width: 1),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AppRadius.button),
          ),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: scheme.primary,
          textStyle: text.labelLarge,
        ),
      ),
      iconButtonTheme: IconButtonThemeData(
        style: IconButton.styleFrom(foregroundColor: scheme.onSurface),
      ),

      // 分段控件（iOS 分段控制）
      segmentedButtonTheme: SegmentedButtonThemeData(
        style: SegmentedButton.styleFrom(
          backgroundColor: fill,
          foregroundColor: scheme.onSurfaceVariant,
          selectedBackgroundColor: scheme.surface,
          selectedForegroundColor: scheme.onSurface,
          side: BorderSide.none,
          textStyle: text.labelMedium,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(9)),
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
        ),
      ),

      // 底部导航（iOS 标签栏观感：白底 + 细分割线 + 强调色指示）
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: Colors.transparent,
        elevation: 0,
        height: 48,
        indicatorColor: scheme.primary.withValues(alpha: 0.16),
        surfaceTintColor: Colors.transparent,
        iconTheme: WidgetStateProperty.resolveWith((states) {
          final selected = states.contains(WidgetState.selected);
          return IconThemeData(
            size: 22,
            color: selected ? scheme.primary : scheme.onSurfaceVariant,
          );
        }),
        labelTextStyle: WidgetStateProperty.resolveWith((states) {
          final selected = states.contains(WidgetState.selected);
          return TextStyle(
            fontSize: 10,
            fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
            color: selected ? scheme.primary : scheme.onSurfaceVariant,
          );
        }),
      ),

      chipTheme: ChipThemeData(
        backgroundColor: fill,
        selectedColor: scheme.primary.withValues(alpha: 0.15),
        labelStyle: text.labelMedium,
        secondaryLabelStyle: text.labelMedium?.copyWith(color: scheme.primary),
        side: BorderSide.none,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadius.pill),
        ),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      ),

      listTileTheme: ListTileThemeData(
        iconColor: scheme.onSurfaceVariant,
        textColor: scheme.onSurface,
        subtitleTextStyle: text.bodySmall,
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      ),

      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) return Colors.white;
          return null;
        }),
        trackColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) return AppColors.success;
          return fill;
        }),
        trackOutlineColor: WidgetStatePropertyAll(Colors.transparent),
      ),

      floatingActionButtonTheme: FloatingActionButtonThemeData(
        backgroundColor: scheme.primary,
        foregroundColor: Colors.white,
        elevation: 2,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
      ),

      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        backgroundColor: const Color(0xFF1C1C1E),
        contentTextStyle: const TextStyle(color: Colors.white, fontSize: 14),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        insetPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      ),

      dialogTheme: DialogThemeData(
        backgroundColor: AppTheme.glassSurface(b),
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(18),
          side: BorderSide(color: AppTheme.glassHighlight(b), width: 0.5),
        ),
        titleTextStyle: text.titleMedium,
        contentTextStyle: text.bodyMedium,
      ),

      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: AppTheme.glassSurface(b),
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: const BorderRadius.vertical(
            top: Radius.circular(AppRadius.sheet),
          ),
          side: BorderSide(color: AppTheme.glassHighlight(b), width: 0.5),
        ),
      ),

      progressIndicatorTheme: ProgressIndicatorThemeData(color: scheme.primary),

      // iOS 滑动转场 + 边缘返回手势（仅作用于 push 出来的页面）
      pageTransitionsTheme: const PageTransitionsTheme(
        builders: {
          TargetPlatform.android: CupertinoPageTransitionsBuilder(),
          TargetPlatform.iOS: CupertinoPageTransitionsBuilder(),
          TargetPlatform.windows: CupertinoPageTransitionsBuilder(),
          TargetPlatform.macOS: CupertinoPageTransitionsBuilder(),
          TargetPlatform.linux: CupertinoPageTransitionsBuilder(),
        },
      ),
    );
  }
}
