"""方法口径常量。

所有判定阈值集中在此定义，并随每份报告快照保存，
使发布后的报告可以还原当时的判定口径。
"""

METHOD_ID = "aq-trend/v1"

# 单日有效：有效小时数下限
MIN_VALID_HOURS = 18

# 月度覆盖等级：有效天数占应测天数比例
COVERAGE_SUFFICIENT = 0.80
COVERAGE_LIMITED = 0.50

# 气象校正：段内拟合所需的最少有效日数
MIN_FIT_POINTS = 30

# 月均值变化显著性阈值（µg/m³）
SIGNIFICANT_CHANGE_UG = 5.0
# 扣除气象影响后残余变化的噪声带宽（µg/m³）
EPSILON_UG = 2.0

# 月均浓度达标限值（µg/m³，演示口径）
COMPLIANCE_LIMIT_UG = 35.0

# 复核行动的跟进期限（天）
FOLLOW_UP_DAYS = 30


def method_dict():
    """返回可序列化的方法口径说明，嵌入分析与报告快照。"""
    return {
        "id": METHOD_ID,
        "valid_day_min_hours": MIN_VALID_HOURS,
        "coverage_sufficient_ratio": COVERAGE_SUFFICIENT,
        "coverage_limited_ratio": COVERAGE_LIMITED,
        "met_adjustment": (
            "段内普通最小二乘：pm25 ~ 1 + temp_c + wind_m_s + rain_mm；"
            "气象效应 = 当月逐日拟合均值 - 段内参考气象拟合值；"
            "校正值 = 实测均值 - 气象效应"
        ),
        "min_fit_points": MIN_FIT_POINTS,
        "significant_change_ug": SIGNIFICANT_CHANGE_UG,
        "epsilon_ug": EPSILON_UG,
        "compliance_limit_ug": COMPLIANCE_LIMIT_UG,
        "follow_up_days": FOLLOW_UP_DAYS,
    }
