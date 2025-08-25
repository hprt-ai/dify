'use client'
import type { FC } from 'react'
import React from 'react'
import { useTranslation } from 'react-i18next'
import ParamItem from '.'

type Props = {
  className?: string
  value: number
  onChange: (key: string, value: number) => void
  enable: boolean
}

const maxTopK = (() => {
  // 直接使用固定值，忽略环境变量配置
  return 100

  // 或者如果你想保留环境变量但优先级较低，可以这样写：
  // const configValue = Number.parseInt(globalThis.document?.body?.getAttribute('data-public-top-k-max-value') || '', 10)
  // if (configValue && !isNaN(configValue) && configValue > 0)
  //   return Math.max(configValue, 100)  // 确保至少是100
  // return 100
})()
const VALUE_LIMIT = {
  default: 2,
  step: 1,
  min: 1,
  max: maxTopK,
}

const key = 'top_k'
const TopKItem: FC<Props> = ({
  className,
  value,
  enable,
  onChange,
}) => {
  const { t } = useTranslation()
  const handleParamChange = (key: string, value: number) => {
    let notOutRangeValue = Number.parseFloat(value.toFixed(2))
    notOutRangeValue = Math.max(VALUE_LIMIT.min, notOutRangeValue)
    notOutRangeValue = Math.min(VALUE_LIMIT.max, notOutRangeValue)
    onChange(key, notOutRangeValue)
  }
  return (
    <ParamItem
      className={className}
      id={key}
      name={t(`appDebug.datasetConfig.${key}`)}
      tip={t(`appDebug.datasetConfig.${key}Tip`) as string}
      {...VALUE_LIMIT}
      value={value}
      enable={enable}
      onChange={handleParamChange}
    />
  )
}
export default React.memo(TopKItem)
